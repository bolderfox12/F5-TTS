"""Review the cast and transcript before generating a dubbed video."""

import json
from collections import Counter, deque
from datetime import datetime
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from nicegui import app, ui

from f5_tts.studio import chatterbox_backend as chatterbox, gemini_tts, google_cloud_tts
from f5_tts.studio.adr_panel import take_panel
from f5_tts.studio.adr_recorder import ADRRecorder
from f5_tts.studio.demucs_backend import demucs_command
from f5_tts.studio.dubbing_activity import observe_requests
from f5_tts.studio.dubbing_review import (
    GEMINI_VOICE_MODELS,
    DubbingProject,
    ExportRequest,
    ReviewLine,
    VoiceRequest,
)
from f5_tts.studio.dubbing_review_api import register_dubbing_routes
from f5_tts.studio.dubbing_review_panel import speech_card
from f5_tts.studio.dubbing_transcript import needs_translation_review, same_timed_line, transcript_warnings
from f5_tts.studio.local_tools import srt_time
from f5_tts.studio.montage_page import send_to_video_editor
from f5_tts.studio.script_review import shorten_dubbing_line
from f5_tts.studio.video_dubbing import (
    GEMINI_TRANSCRIPTION,
    VIDEO_EXTENSIONS,
    VIDEO_LIMIT,
    analyze_video,
    check_vocal_profiles,
    speaker_references,
    translate_dialogue,
)

from f5_tts.studio.video_probe import video_duration
from f5_tts.studio.video_editor_panel import caption_track, original_player, video_editor
from f5_tts.studio.video_timeline import VideoTimeline
from f5_tts.studio.vocal_profiles import PROFILE_LABELS, profile_check_status


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
ACTIVITIES = {
    "analyze_video": "Transcrição e separação de vozes",
    "check_vocal_profiles": "Checagem de perfis vocais",
    "translate_dialogue": "Tradução do roteiro",
    "shorten_dubbing_line": "Ajuste de duração da fala",
    "fit_preview": "Sincronização das falas",
    "regenerate": "Síntese da fala",
    "prepare": "Preparação das falas",
    "export": "Renderização da dublagem",
    "guide": "Separação do áudio de fundo",
    "test_connection": "Teste do Google Cloud",
    "import_credentials": "Importação de credencial do Google Cloud",
    "choose_take": "Seleção de take",
    "forget_take": "Remoção de take",
    "remove_line": "Remoção de fala suspeita",
}


def register_video_dubbing_page(service, shell, studio_action):
    t = service.translate_ui
    projects = {}
    draft_path = service.home / "dubbing-draft.json"
    active_page = None
    register_dubbing_routes(app, projects)

    @ui.page("/video-dubbing")
    def ai_page():
        video_dubbing_page(False)

    @ui.page("/video-dubbing-human")
    def human_page():
        video_dubbing_page(True)

    def video_dubbing_page(initial_human):
        nonlocal active_page
        page = object()
        active_page = page
        human = initial_human
        take_offsets = {}
        source = None
        segments = []
        dubbing_range = None
        analyzed_language = None
        busy = False
        translation_key = None
        dialogue = []
        original_timeline = None
        project = None
        previews = {}
        cloud_cards = {}
        last_cloud_revision = None
        gemini_cast = {}
        shorter_lines = {}
        original = None
        source_name = ""
        profile_filter = "all"
        last_saved = None
        activity_events = deque(maxlen=1000)
        activity_totals = Counter()
        last_progress_log = None

        def record_activity(message, **totals):
            activity_events.append((datetime.now().strftime("%H:%M:%S"), message, totals))

        def refresh_activity():
            refresh_cloud_cards()
            if not activity_events:
                return
            while activity_events:
                stamp, message, totals = activity_events.popleft()
                activity_totals.update(totals)
                activity_log.push(f"[{stamp}] {message}")
            activity_summary.set_text(
                t(
                    "Nesta aba: {value_0} chamadas de API, {value_1} falhas, {value_2} tokens informados, "
                    "{value_3} caracteres enviados à síntese.",
                    value_0=activity_totals["calls"],
                    value_1=activity_totals["failed"],
                    value_2=activity_totals["tokens"],
                    value_3=activity_totals["characters"],
                )
            )

        def clear_activity():
            activity_events.clear()
            activity_totals.clear()
            activity_log.clear()
            record_activity(t("Console limpo."))
            refresh_activity()

        async def action(fn, *args, **kwargs):
            label = ACTIVITIES.get(getattr(fn, "__name__", ""))

            def run_operation():
                started = perf_counter()
                if label:
                    record_activity(t(label) + " · " + t("Iniciada"))
                with observe_requests(record_activity):
                    try:
                        output = fn(*args, **kwargs)
                    except Exception as error:
                        if label:
                            record_activity(f"{t(label)} · {t('Falhou')} ({type(error).__name__})")
                        raise
                if label:
                    record_activity(f"{t(label)} · {t('Concluída')} · {perf_counter() - started:.2f} s")
                return output

            try:
                return await studio_action(run_operation)
            finally:
                refresh_activity()

        def save_draft(*, manual=False):
            nonlocal last_saved
            if active_page is not page:
                if manual:
                    ui.notify(t("Atualize esta aba para abrir o rascunho mais recente."), type="warning")
                return
            if source is None or busy:
                return
            state = {
                "source": str(source),
                "source_name": source_name,
                "dubbing_range": list(dubbing_range) if dubbing_range else None,
                "rows": [
                    {**row, "reference": str(row["reference"])} if row.get("reference") else dict(row)
                    for row in segments
                ],
                "dialogue": list(dialogue),
                "analyzed_language": analyzed_language,
                "project": project.draft_state() if project else None,
                "take_offsets": {str(identifier): offset for identifier, offset in take_offsets.items()},
                "gemini_cast": {str(speaker): voice for speaker, voice in gemini_cast.items()},
                "settings": {name: control.value for name, control in draft_settings.items()},
            }
            if state != last_saved:
                try:
                    service._write_json(draft_path, state)
                except OSError:
                    draft_status.set_text(t("Não foi possível salvar o rascunho."))
                    return
                last_saved = state
            draft_status.set_text(t("Rascunho salvo neste Studio."))
            if manual:
                ui.notify(t("Rascunho salvo neste Studio."), type="positive")

        def new_dubbing():
            nonlocal active_page
            if active_page is not page or busy:
                return
            try:
                draft_path.unlink(missing_ok=True)
            except OSError:
                ui.notify(t("Não foi possível excluir o rascunho."), type="negative")
                return
            if project:
                projects.pop(project.id, None)
            active_page = None
            ui.navigate.to("/video-dubbing-human" if human else "/video-dubbing")

        def show_original():
            nonlocal original, original_timeline
            source_preview.clear()
            with source_preview:
                ui.label(t("Vídeo original")).classes("text-lg font-semibold")
                original = original_player(service, action, source)
                original_timeline = VideoTimeline(
                    t, original, select_range=True, on_range=update_dubbing_range
                ).classes("w-full")
                original_timeline.set_range(dubbing_range)
                if segments:
                    original_timeline.set_tracks(original=[caption_track(t("Transcrição original"), segments)])
                display_dubbing_range()
            recorder.set_video(original)
            upload_status.set_text(source_name)
            source_preview.set_visibility(True)
            uploader.disable()
            save_button.enable()
            new_button.enable()

        def display_dubbing_range():
            if dubbing_range:
                start, end = dubbing_range
                range_status.set_text(
                    t("Dublagem entre {value_0} e {value_1}", value_0=srt_time(start), value_1=srt_time(end))
                )
            else:
                range_status.set_text(t("Dublagem do vídeo inteiro"))

        def update_dubbing_range(event):
            nonlocal dubbing_range
            values = event.args
            try:
                start, end = float(values["start"]), float(values["end"])
                duration = float(values["duration"])
            except (KeyError, TypeError, ValueError):
                return
            if not 0 <= start < end <= duration:
                return
            dubbing_range = (start, end)
            display_dubbing_range()
            record_activity(
                t("Trecho para dublar: {value_0} – {value_1}", value_0=srt_time(start), value_1=srt_time(end))
            )
            save_draft()

        def dub_full_video():
            nonlocal dubbing_range
            dubbing_range = None
            if original_timeline:
                original_timeline.set_range(None)
            display_dubbing_range()
            record_activity(t("Dublagem do vídeo inteiro"))
            save_draft()

        def clear_result():
            result.clear()
            render_status.set_text("")
            source_preview.set_visibility(source is not None)

        progress_state = (0, "", {})
        progress_running = False

        def update_progress(value, message, **values):
            nonlocal progress_state, last_progress_log
            progress_state = value, message, values
            text = t(message, **values)
            bucket = int(value * 20)
            if (bucket, text) != last_progress_log:
                last_progress_log = (bucket, text)
                record_activity(f"{round(value * 100)}% · {text}")

        def redraw_progress():
            value, message, values = progress_state
            progress_bar.set_value(value)
            text = t(message, **values)
            progress_label.set_text(text)
            console_progress_bar.set_value(value)
            if progress_running and not value:
                console_progress_bar.props("indeterminate")
            else:
                console_progress_bar.props(remove="indeterminate")
            console_progress_label.set_text(f"{round(value * 100)}% · {text}")

        def begin_progress(message):
            nonlocal last_progress_log, progress_running
            last_progress_log = None
            progress_running = True
            update_progress(0, message)
            redraw_progress()
            progress_corner.set_visibility(True)
            progress_timer.activate()

        def end_progress():
            nonlocal progress_running
            progress_running = False
            redraw_progress()
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

        def restore_draft():
            nonlocal source, source_name, project, analyzed_language, last_saved, dubbing_range
            try:
                state = json.loads(draft_path.read_text(encoding="utf-8"))
                video = service.managed_upload(state["source"])
                rows = state["rows"]
                restored = (
                    DubbingProject.restore_draft(service, video, rows, state["project"]) if state["project"] else None
                )
            except FileNotFoundError:
                return
            except (OSError, KeyError, TypeError, ValueError):
                draft_status.set_text(t("Não foi possível reabrir o rascunho."))
                new_button.enable()
                return
            for name, control in draft_settings.items():
                if name in state["settings"]:
                    value = state["settings"][name]
                    if not hasattr(control, "options") or value in control.options:
                        control.set_value(value)
            source, source_name, project = video, state["source_name"], restored
            saved_range = state.get("dubbing_range")
            dubbing_range = tuple(map(float, saved_range)) if saved_range else None
            analyzed_language = state["analyzed_language"]
            segments.extend(rows)
            dialogue.extend(state["dialogue"])
            take_offsets.update({UUID(identifier): value for identifier, value in state["take_offsets"].items()})
            gemini_cast.update({int(speaker): voice for speaker, voice in state.get("gemini_cast", {}).items()})
            if project:
                projects[project.id] = project
            last_saved = state
            show_original()
            set_busy(False)

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
            with ui.row().classes("w-full items-center"):
                save_button = ui.button(
                    t("Salvar dublagem"), icon="save", on_click=lambda: save_draft(manual=True)
                ).props("outline no-caps")
                new_button = ui.button(t("Nova dublagem"), icon="add", on_click=lambda: discard_dialog.open()).props(
                    "flat no-caps"
                )
                draft_status = ui.label().classes("studio-muted")
            save_button.disable()
            new_button.disable()
            with ui.dialog() as discard_dialog, ui.card():
                ui.label(t("Excluir o rascunho atual e iniciar uma nova dublagem?"))
                with ui.row():
                    ui.button(t("Cancelar"), on_click=discard_dialog.close).props("flat no-caps")
                    ui.button(t("Excluir rascunho"), on_click=new_dubbing, color="negative").props("no-caps")
            with ui.card().classes("studio-card w-full"):
                ui.label(t("Vídeo e modelo de transcrição")).classes("text-lg font-semibold")
                upload_status = ui.label(t("Envie um vídeo MP4, MOV, MKV ou WebM de até 500 MB."))

                async def receive(event):
                    nonlocal source, busy, project, source_name, dubbing_range
                    if busy or source is not None or active_page is not page:
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
                        record_activity(f"{t('Falha ao receber o vídeo')} ({type(error).__name__})")
                        ui.notify(t(str(error)), type="negative")
                    else:
                        record_activity(t("Vídeo recebido e pronto para transcrição."))
                        source = path
                        source_name = event.file.name
                        dubbing_range = None
                        project = None
                        previews.clear()
                        shorter_lines.clear()
                        take_offsets.clear()
                        segments.clear()
                        dialogue.clear()
                        update_speakers.set_value(False)
                        update_speakers.set_visibility(False)
                        review.refresh()
                        clear_result()
                        show_original()
                        status.set_text("")
                        mix.set_visibility(False)
                    finally:
                        busy = False
                        add_line_button.set_enabled(source is not None)
                        guide_button.set_enabled(source is not None)
                        analyze.set_enabled(source is not None)
                        uploader.reset()
                        save_draft()

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
                    uploader.set_enabled(not value and source is None)
                    save_button.set_enabled(not value and source is not None)
                    new_button.set_enabled(not value and source is not None)
                    origin.set_enabled(not value)
                    input_model.set_enabled(not value)
                    expected_voices.set_enabled(not value)
                    separate_vocals.set_enabled(not value)
                    recover_missing.set_enabled(not value)
                    update_speakers.set_enabled(not value)
                    update_speakers.set_visibility(bool(project and segments))
                    gemini_key.set_enabled(not value)
                    analyze.set_enabled(not value and source is not None)
                    destination.set_enabled(not value)
                    translation_provider.set_enabled(not value)
                    sync_translation.set_enabled(not value)
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
                    if not value:
                        save_draft()

                async def transcribe():
                    nonlocal analyzed_language, project
                    if busy or source is None:
                        return
                    if not rebuild_lines.value and any(row["start"] is None or row["end"] is None for row in segments):
                        return ui.notify(
                            t("Preencha os tempos das falas antes de atualizar a transcrição."), type="warning"
                        )
                    source_language = origin.value
                    count = expected_voices.value
                    if count is not None and (count < 1 or int(count) != count):
                        return ui.notify(
                            t("Informe um número inteiro de vozes ou deixe a quantidade em branco."), type="warning"
                        )
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
                            speaker_count=int(count) if count is not None else None,
                            separate_vocals=separate_vocals.value,
                            time_range=dubbing_range,
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
                        detected = len({row["speaker"] for row in recognized})
                        if count is not None and detected != count:
                            ui.notify(
                                t(
                                    "Foram identificadas {value_0} vozes. Confira os personagens nas falas.",
                                    value_0=detected,
                                ),
                                type="warning",
                            )
                        if project is None or rebuild_lines.value:
                            replacement = await action(DubbingProject, service, source, recognized, duration)
                            if replacement is None:
                                return
                            retained = {
                                (round(row["start"], 1), round(row["end"], 1), row["text"]): translation
                                for row, translation in zip(segments, dialogue, strict=True)
                                if row["start"] is not None and row["end"] is not None
                            }
                            if project:
                                projects.pop(project.id, None)
                            project = replacement
                            projects[project.id] = project
                            segments[:] = recognized
                            dialogue[:] = [
                                retained.get((round(row["start"], 1), round(row["end"], 1), row["text"]), "")
                                for row in recognized
                            ]
                            previews.clear()
                            shorter_lines.clear()
                            take_offsets.clear()
                            clear_result()
                        else:

                            def overlap(first, second):
                                return min(first["end"], second["end"]) - max(first["start"], second["start"])

                            existing = list(zip(project.rows, segments, strict=True))
                            for identifier, row in existing:
                                sample = max(recognized, key=lambda candidate: overlap(row, candidate))
                                shared = overlap(row, sample)
                                if update_speakers.value and shared > 0:
                                    coverage = Counter()
                                    for candidate in recognized:
                                        coverage[candidate["speaker"]] += max(0, overlap(row, candidate))
                                    speaker, seconds = coverage.most_common(1)[0]
                                    if seconds > (row["end"] - row["start"]) / 2:
                                        if speaker != row["speaker"]:
                                            row["speaker"] = speaker
                                            row.pop("preferred_reference", None)
                                        if not row.get("vocal_profile_manual"):
                                            matching = max(
                                                (item for item in recognized if item["speaker"] == speaker),
                                                key=lambda item: overlap(row, item),
                                            )
                                            row["vocal_profile"] = matching.get("vocal_profile", "unknown")
                                        if row.get("reference"):
                                            project.attach_reference(identifier, row)
                                if shared > 0 and not row.get("reference"):
                                    row["reference"] = sample["reference"]
                                    row["reference_duration"] = sample["reference_duration"]
                                    row["reference_transcript"] = sample["text"]
                                    row["text"] = row["text"] or sample["text"]
                                    project.attach_reference(identifier, row)
                            for sample in recognized:
                                if all(
                                    overlap(row, sample) <= 0 and not same_timed_line(row, sample) for row in segments
                                ):
                                    project.add_line(sample)
                                    segments.append(sample)
                                    dialogue.append("")
                            if update_speakers.value:
                                previews.clear()
                                shorter_lines.clear()
                                clear_result()
                                update_speakers.set_value(False)
                        analyzed_language = source_language
                        if original_timeline:
                            original_timeline.set_tracks(original=[caption_track(t("Transcrição original"), segments)])
                    finally:
                        end_progress()
                        status.set_text("")
                        set_busy(False)

                def open_analysis():
                    if busy or source is None:
                        return
                    rebuild_lines.set_value(False)
                    rebuild_lines.set_visibility(bool(project and segments and not any(project.takes.values())))
                    speaker_dialog.open()

                async def start_analysis(automatic=False):
                    if automatic:
                        expected_voices.set_value(None)
                    elif expected_voices.value is None or expected_voices.value < 1 or expected_voices.value % 1:
                        ui.notify(t("Informe uma quantidade inteira de personagens."), type="warning")
                        return
                    speaker_dialog.close()
                    await transcribe()

                with ui.dialog() as speaker_dialog, ui.card().classes("studio-card w-full max-w-md"):
                    ui.label(t("Quantos personagens falam nesta cena?")).classes("text-lg font-semibold")
                    expected_voices = ui.number(t("Quantidade de personagens"), min=1, step=1).props("outlined")
                    rebuild_lines = ui.checkbox(
                        t("Recriar em falas curtas. Os trechos divididos precisam de nova tradução.")
                    )
                    with ui.row():
                        ui.button(t("Analisar com essa quantidade"), on_click=start_analysis).props("no-caps")
                        ui.button(t("Detectar pelo áudio"), on_click=lambda: start_analysis(automatic=True)).props(
                            "outline no-caps"
                        )
                        ui.button(t("Cancelar"), on_click=speaker_dialog.close).props("flat no-caps")
                recover_missing = ui.checkbox(t("Revisar trechos sem transcrição"), value=True)
                separate_vocals = ui.checkbox(t("Separar voz da música e dos efeitos antes de transcrever"))
                ui.label(t("A prévia e a exportação misturam a dublagem com o fundo separado.")).classes(
                    "studio-muted"
                ).bind_visibility_from(separate_vocals, "value")
                update_speakers = ui.checkbox(t("Atualizar personagens das falas existentes"))
                update_speakers.set_visibility(False)
                analyze = ui.button(t("Transcrever e separar vozes"), icon="subtitles", on_click=open_analysis)
                analyze.disable()
                status = ui.label("").classes("studio-muted")
                with ui.row().classes("w-full items-center gap-3"):
                    range_status = ui.label(t("Dublagem do vídeo inteiro")).classes("studio-muted")
                    ui.button(t("Usar vídeo inteiro"), icon="restart_alt", on_click=dub_full_video).props(
                        "flat no-caps"
                    )

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

                def update_takes():
                    save_draft()
                    review.refresh()

                recorder = ADRRecorder(t, lambda event: set_busy(bool(event.args)), update_takes).classes("w-full")
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
                        {
                            **service.model_choices(),
                            gemini_tts.MODEL_ID: t("Gemini TTS · vozes expressivas"),
                            google_cloud_tts.MODEL_ID: t("Google Cloud Text-to-Speech"),
                        },
                        value=chatterbox.MODEL_ID,
                        label=t("Modelo de dublagem"),
                    ).props("outlined")
                ui.label(t("Gemini TTS envia a fala traduzida e a direção de voz ao Google.")).classes(
                    "studio-muted"
                ).bind_visibility_from(output_model, "value", lambda value: value == gemini_tts.MODEL_ID)
                with (
                    ui.row()
                    .classes("w-full items-center")
                    .bind_visibility_from(output_model, "value", lambda value: value == google_cloud_tts.MODEL_ID)
                ):
                    ui.button(t("Conectar Google Cloud"), icon="cloud", on_click=lambda: cloud_dialog.open()).props(
                        "outline no-caps"
                    )
                    ui.label(
                        t("O texto e a entonação são enviados ao Google. O uso conta na cota do projeto.")
                    ).classes("studio-muted")
                with ui.dialog() as cloud_dialog, ui.card().classes("studio-card w-full max-w-lg"):
                    ui.label(t("Conectar Google Cloud")).classes("text-lg font-semibold")
                    ui.label(
                        t(
                            "Importe a chave JSON de uma conta de serviço do seu projeto ou use as credenciais ADC da máquina."
                        )
                    )
                    ui.link(
                        t("Abrir contas de serviço no Google Cloud"),
                        "https://console.cloud.google.com/iam-admin/serviceaccounts",
                        new_tab=True,
                    )
                    cloud_status = ui.label().classes("studio-muted")

                    def show_cloud_status():
                        imported = google_cloud_tts.credential_file(service).is_file()
                        cloud_status.set_text(
                            t(
                                "Chave importada neste Studio."
                                if imported
                                else "O Studio usará as credenciais ADC da máquina."
                            )
                        )
                        remove_cloud_key.set_visibility(imported)

                    async def import_cloud_key(event):
                        try:
                            if await action(google_cloud_tts.import_credentials, service, await event.file.read()):
                                show_cloud_status()
                                ui.notify(t("Chave do Google Cloud importada."), type="positive")
                        finally:
                            cloud_upload.reset()

                    async def test_cloud():
                        if busy:
                            return
                        test_cloud_button.disable()
                        set_busy(True)
                        begin_progress("Testando o Google Cloud…")
                        try:
                            if await action(google_cloud_tts.test_connection, service):
                                update_progress(1, "Áudio de teste gerado.")
                                ui.notify(t("Google Cloud conectado. O áudio de teste foi gerado."), type="positive")
                        finally:
                            end_progress()
                            set_busy(False)
                            test_cloud_button.enable()

                    def remove_cloud():
                        try:
                            google_cloud_tts.credential_file(service).unlink(missing_ok=True)
                        except OSError:
                            ui.notify(t("Não foi possível remover a chave importada."), type="negative")
                            return
                        confirm_cloud_removal.close()
                        show_cloud_status()

                    cloud_upload = (
                        ui.upload(
                            label=t("Importar chave JSON"),
                            auto_upload=True,
                            max_file_size=64 * 1024,
                            on_upload=import_cloud_key,
                        )
                        .props("accept=.json")
                        .classes("w-full")
                    )
                    ui.label(t("O teste gera uma fala curta e usa a cota do projeto.")).classes("studio-muted")
                    with ui.row():
                        test_cloud_button = ui.button(t("Testar conexão"), on_click=test_cloud).props("no-caps")
                        remove_cloud_key = ui.button(
                            t("Remover chave importada"), on_click=lambda: confirm_cloud_removal.open()
                        ).props("flat no-caps")
                        ui.button(t("Fechar"), on_click=cloud_dialog.close).props("flat no-caps")
                with ui.dialog() as confirm_cloud_removal, ui.card():
                    ui.label(t("Remover a chave salva neste Studio?"))
                    with ui.row():
                        ui.button(t("Cancelar"), on_click=confirm_cloud_removal.close).props("flat no-caps")
                        ui.button(t("Remover"), on_click=remove_cloud, color="negative").props("no-caps")
                show_cloud_status()
                with (
                    ui.column()
                    .classes("w-full gap-1")
                    .bind_visibility_from(translation_provider, "value", lambda value: value == "gemini")
                ):
                    sync_translation = ui.checkbox(t("Adaptar tradução ao ritmo da voz original"))
                    ui.label(
                        t("O roteiro é enviado ao Google. Com a adaptação ativa, o áudio original também é enviado.")
                    ).classes("studio-muted")

                def show_gemini_key():
                    gemini_options.set_visibility(
                        input_model.value == GEMINI_TRANSCRIPTION
                        or translation_provider.value == "gemini"
                        or output_model.value == gemini_tts.MODEL_ID
                        or auto_fit.value
                    )

                input_model.on_value_change(show_gemini_key)
                translation_provider.on_value_change(show_gemini_key)
                output_model.on_value_change(show_gemini_key)

                treat_audio = ui.switch(t("Tratar o áudio da dublagem"), value=True)
                ui.label(t("Reduz graves indesejados, equilibra o volume e controla picos da voz.")).classes(
                    "studio-muted"
                )

                async def check_profiles():
                    if busy or source is None or not segments:
                        return
                    set_busy(True)
                    begin_progress("Conferindo perfis no áudio original…")
                    try:
                        checks = await action(
                            check_vocal_profiles,
                            service,
                            source,
                            [dict(row) for row in segments],
                            on_progress=update_progress,
                        )
                        if checks is not None:
                            for row, check in zip(segments, checks, strict=True):
                                row["vocal_check"] = check
                            update_progress(1, "Perfis vocais conferidos.")
                    finally:
                        end_progress()
                        set_busy(False)

                def cloud_audio(identifier, index, transcript_issue):
                    payload = voice_request(index)
                    if not payload["text"].strip():
                        return None, ""
                    if needs_translation_review(segments[index], payload["text"], transcript_issue):
                        return None, "Revisar texto"
                    try:
                        clip, active = project.cloud_voice(identifier, VoiceRequest(**payload))
                        if clip:
                            path, duration = clip
                            clip = (f"{project.url(path)}?v={path.stat().st_mtime_ns}", duration)
                    except (OSError, ValueError):
                        return None, "Pendente"
                    return clip, "Gerando…" if active else "OK" if clip else "Pendente"

                def refresh_cloud_cards():
                    nonlocal last_cloud_revision
                    if project is None or not cloud_cards:
                        return
                    revision = project.id, project.voice_revision
                    if revision == last_cloud_revision:
                        return
                    last_cloud_revision = revision
                    for identifier, (index, issue, update) in cloud_cards.items():
                        update(*cloud_audio(identifier, index, issue))

                def confirm_remove_line(identifier):
                    with ui.dialog() as dialog, ui.card():
                        ui.label(t("Excluir esta fala do rascunho?"))

                        async def remove():
                            if busy or identifier not in project.rows:
                                return
                            index = list(project.rows).index(identifier)
                            set_busy(True)
                            try:
                                if await action(project.remove_line, identifier):
                                    segments.pop(index)
                                    dialogue.pop(index)
                                    previews.pop(identifier, None)
                                    shorter_lines.pop(identifier, None)
                                    take_offsets.pop(identifier, None)
                                    clear_result()
                                    if original_timeline:
                                        original_timeline.set_tracks(
                                            original=[caption_track(t("Transcrição original"), segments)]
                                        )
                                    dialog.close()
                            finally:
                                set_busy(False)

                        with ui.row():
                            ui.button(t("Cancelar"), on_click=dialog.close).props("flat no-caps")
                            ui.button(t("Excluir fala"), on_click=remove, color="negative").props("no-caps")
                    dialog.open()

                @ui.refreshable
                def review():
                    cloud_cards.clear()
                    if not segments or project is None:
                        return
                    reference_segments = [row for row in segments if row.get("reference")]
                    using_gemini_tts = output_model.value in GEMINI_VOICE_MODELS
                    if not human and reference_segments and not using_gemini_tts:
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
                                        if row["speaker"] == speaker and row.get("reference")
                                    }
                                    player = ui.audio(str(segment["reference"])).classes("max-w-full")

                                    def choose(event, actor=speaker, audio=player):
                                        selected = segments[event.value]
                                        for row in segments:
                                            if row["speaker"] == actor:
                                                row["preferred_reference"] = row is selected
                                        audio.set_source(str(selected["reference"]))
                                        save_draft()
                                        review.refresh()

                                    ui.select(
                                        candidates,
                                        value=next(i for i in candidates if segments[i] is segment),
                                        label=t("Trecho de referência"),
                                        on_change=choose,
                                    ).props("outlined dense").classes("w-64").set_enabled(not busy)

                    if not human and using_gemini_tts:
                        with ui.row().classes("w-full"):
                            for speaker in sorted({row["speaker"] for row in segments}):

                                def choose_voice(event, actor=speaker):
                                    gemini_cast[actor] = event.value
                                    save_draft()
                                    clear_result()
                                    review.refresh()

                                ui.select(
                                    list(gemini_tts.VOICES),
                                    value=gemini_cast.get(speaker, gemini_tts.default_voice(speaker)),
                                    label=t("Voz do personagem {value_0}", value_0=speaker),
                                    on_change=choose_voice,
                                ).props("outlined dense").classes("w-48").set_enabled(not busy)
                    elif not human:
                        references()
                    ui.label(t("Editor e revisão de dublagem")).classes("text-lg font-semibold")

                    def change_filter(event):
                        nonlocal profile_filter
                        profile_filter = event.value
                        review.refresh()

                    statuses = Counter(profile_check_status(row) for row in segments)
                    with ui.row().classes("w-full items-center"):
                        ui.select(
                            {
                                "all": t("Todas as vozes"),
                                **{key: t(label) for key, label in PROFILE_LABELS.items()},
                                "review": t("Falas para revisar"),
                            },
                            value=profile_filter,
                            label=t("Filtrar por perfil vocal"),
                            on_change=change_filter,
                        ).props("outlined dense").set_enabled(not busy)
                        ui.button(t("Checar perfis vocais"), icon="fact_check", on_click=check_profiles).props(
                            "outline no-caps"
                        ).set_enabled(not busy)
                    if statuses["match"] + statuses["mismatch"] + statuses["unclear"]:
                        ui.label(
                            t(
                                "Checagem de perfis: {value_0} compatíveis, {value_1} divergentes, {value_2} inconclusivas.",
                                value_0=statuses["match"],
                                value_1=statuses["mismatch"],
                                value_2=statuses["unclear"],
                            )
                        ).classes("studio-muted")
                    if profile_filter == "review" and not (statuses["mismatch"] or statuses["unclear"]):
                        ui.label(t("Nenhuma fala sinalizada para revisão.")).classes("studio-muted")
                    if not human:
                        ui.button(t("Traduzir roteiro"), icon="translate", on_click=translate).set_enabled(not busy)
                        ui.button(t("Gerar prévias das falas"), icon="record_voice_over", on_click=prepare).props(
                            "outline no-caps"
                        ).set_enabled(not busy)
                    speakers = sorted({row["speaker"] for row in segments})
                    transcript_issues = transcript_warnings(segments)
                    options = {speaker: t("Personagem {value_0}", value_0=speaker) for speaker in speakers}
                    options[max(speakers) + 1] = t("Novo personagem")
                    indexed_rows = list(enumerate(zip(project.rows, segments, strict=True)))
                    ordered_rows = sorted(
                        indexed_rows,
                        key=lambda item: item[1][1]["start"] if item[1][1]["start"] is not None else float("inf"),
                    )
                    for number, (index, (identifier, row)) in enumerate(ordered_rows, 1):
                        if profile_filter == "review":
                            if profile_check_status(row) not in ("mismatch", "unclear"):
                                continue
                        elif profile_filter != "all" and row.get("vocal_profile", "unknown") != profile_filter:
                            continue

                        def change(field, value, i=index, item=row, key=identifier):
                            if field == "translated":
                                dialogue[i] = value or ""
                            elif field == "speaker" and value is not None:
                                item["speaker"] = value
                                item.pop("preferred_reference", None)
                                project.attach_reference(key, item)
                                clear_result()
                                review.refresh()
                            elif field == "text":
                                item["text"] = value or ""
                            elif field == "include_in_dub":
                                item["include_in_dub"] = bool(value)
                                clear_result()
                            elif field == "gemini_style":
                                item["gemini_style"] = value or ""
                                clear_result()
                            elif field == "confirm_repetition":
                                item["approved_repetition"] = [
                                    dialogue[i].strip(),
                                    item["text"],
                                    item["start"],
                                    item["end"],
                                ]
                                review.refresh()
                            elif field == "vocal_profile" and value in PROFILE_LABELS:
                                for segment_id, segment in zip(project.rows, segments, strict=True):
                                    if segment["speaker"] == item["speaker"]:
                                        segment["vocal_profile"] = value
                                        segment["vocal_profile_manual"] = True
                                        project.attach_reference(segment_id, segment)
                                review.refresh()
                            if field in ("translated", "gemini_style") and key in cloud_cards:
                                _, issue, update = cloud_cards[key]
                                update(*cloud_audio(key, i, issue))
                            save_draft()

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

                        def remove(identifier=identifier):
                            confirm_remove_line(identifier)

                        voice = voice_request(index)
                        preview = previews.get(identifier)
                        if preview and preview[0] != voice:
                            preview = None
                        clip = preview[1] if preview else None
                        synthesis_state = None
                        if not human and using_gemini_tts and identifier not in project.active_takes:
                            clip, synthesis_state = cloud_audio(identifier, index, transcript_issues[index])

                        def human_takes(segment_id=identifier, item=row):
                            take_panel(
                                t,
                                project,
                                segment_id,
                                item,
                                recorder,
                                take_offsets,
                                action,
                                review.refresh,
                                busy,
                                save=save_draft,
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
                            save_draft()
                            clear_result()
                            review.refresh()

                        update_synthesis = speech_card(
                            t,
                            row,
                            options,
                            dialogue[index],
                            clip,
                            number=number,
                            change=change,
                            seek=seek,
                            regenerate=regenerate,
                            busy=busy,
                            takes_panel=human_takes if human else None,
                            shorten=shorten if not human and identifier not in project.active_takes else None,
                            suggestions=proposal[1] if proposal and proposal[0] == basis else (),
                            apply=apply_shorter,
                            gemini_style=row.get("gemini_style", "") if not human and using_gemini_tts else None,
                            synthesis_state=synthesis_state,
                            transcript_issue=transcript_issues[index],
                            remove=remove if transcript_issues[index] and not project.takes.get(identifier) else None,
                            include=row.get("include_in_dub", True),
                        )
                        if update_synthesis:
                            cloud_cards[identifier] = (index, transcript_issues[index], update_synthesis)

                def voice_request(index):
                    row = segments[index]
                    if output_model.value in GEMINI_VOICE_MODELS:
                        return {
                            "text": dialogue[index],
                            "speaker": row["speaker"],
                            "language": destination.value,
                            "model": output_model.value,
                            "gemini_voice": gemini_cast.get(row["speaker"], gemini_tts.default_voice(row["speaker"])),
                            "gemini_style": row.get("gemini_style", ""),
                        }
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

                def requested_lines(*, skip_repetitions=False):
                    payload = []
                    issues = transcript_warnings(segments)
                    for index, (identifier, row) in enumerate(zip(project.rows, segments, strict=True)):
                        if not row.get("include_in_dub", True):
                            continue
                        if dubbing_range and (
                            row["start"] is None
                            or row["end"] is None
                            or row["start"] < dubbing_range[0]
                            or row["end"] > dubbing_range[1]
                        ):
                            continue
                        take_id = project.active_takes.get(identifier)
                        if (
                            not take_id
                            and output_model.value in GEMINI_VOICE_MODELS
                            and needs_translation_review(row, dialogue[index], issues[index])
                        ):
                            if skip_repetitions:
                                continue
                            raise ValueError("Revise ou confirme as falas sinalizadas por repetição antes de exportar.")
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
                    if not payload:
                        if not any(row.get("include_in_dub", True) for row in segments):
                            raise ValueError("Marque pelo menos uma fala para incluir na dublagem.")
                        raise ValueError("Nenhuma fala completa está dentro do trecho selecionado na linha do tempo.")
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
                    if not segments[index].get("include_in_dub", True):
                        return ui.notify(t("Ative 'Incluir na dublagem' antes de gerar esta fala."), type="warning")
                    if busy or not dialogue[index].strip():
                        return ui.notify(t("Preencha a tradução desta fala."), type="warning")
                    identifier = list(project.rows)[index]
                    payload = voice_request(index)
                    if payload["model"] in GEMINI_VOICE_MODELS and needs_translation_review(
                        segments[index], payload["text"], transcript_warnings(segments)[index]
                    ):
                        return ui.notify(
                            t("Revise ou confirme a repetição nesta fala antes de gerar o áudio."), type="warning"
                        )
                    try:
                        request = VoiceRequest(**payload)
                    except ValueError as error:
                        return ui.notify(t(str(error)), type="warning")
                    begin_progress("Regerando a fala…")
                    set_busy(True)
                    try:
                        generated = await action(
                            project.regenerate,
                            identifier,
                            request,
                            gemini_key=gemini_key.value,
                            on_progress=update_progress,
                        )
                        if generated:
                            previews[identifier] = (payload, generated)
                    finally:
                        end_progress()
                        set_busy(False)

                async def prepare():
                    if busy:
                        return
                    try:
                        lines = requested_lines(skip_repetitions=True)
                    except ValueError as error:
                        return ui.notify(t(str(error)), type="warning")
                    skipped = len(segments) - len(lines)
                    if skipped:
                        ui.notify(
                            t("{value_0} falas ficaram para revisão de repetição.", value_0=skipped), type="warning"
                        )
                    if not lines:
                        return
                    render_status.set_text(t("Gerando prévias das falas…"))
                    begin_progress("Gerando prévias das falas…")
                    set_busy(True)
                    try:
                        generated = await action(
                            project.prepare, lines, gemini_key=gemini_key.value, on_progress=update_progress
                        )
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
                    begin_progress("Traduzindo o roteiro…")
                    try:
                        translated = await action(
                            translate_dialogue,
                            service,
                            [dict(row) for row in segments],
                            key[0],
                            key[1],
                            provider=translation_provider.value,
                            gemini_key=gemini_key.value if translation_provider.value == "gemini" else "",
                            timing_source=source
                            if translation_provider.value == "gemini" and sync_translation.value
                            else None,
                        )
                        if translated:
                            update_progress(1, "Roteiro traduzido.")
                            dialogue[:] = translated
                            translation_key = key
                            if translation_provider.value == "gemini" and sync_translation.value:
                                auto_fit.set_value(True)
                            clear_result()
                    finally:
                        end_progress()
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
                        save_draft()
                        ui.notify(
                            t("Falas encurtadas automaticamente: {value_0}", value_0=len(changed)), type="positive"
                        )
                    cached = await action(project.prepare, after.lines, gemini_key=gemini_key.value)
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
                            gemini_key=gemini_key.value,
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
                                        gemini_key=gemini_key.value,
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
                    auto_fit.on_value_change(show_gemini_key)
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

            with ui.card().classes("studio-card w-full gap-3"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(t("Console de atividades")).classes("text-lg font-semibold")
                    ui.button(t("Limpar log"), icon="delete_sweep", on_click=clear_activity).props("flat no-caps")
                console_progress_label = ui.label(t("Aguardando atividade.")).classes("font-medium")
                console_progress_bar = ui.linear_progress(value=0).classes("w-full")
                activity_summary = ui.label().classes("studio-muted")
                activity_log = ui.log(max_lines=300).classes(
                    "w-full h-64 rounded-lg bg-slate-950 p-3 text-teal-100 font-mono text-sm"
                )
                record_activity(t("Console pronto."))
                refresh_activity()
                ui.timer(0.25, refresh_activity)

            with ui.page_sticky(position="bottom-right", x_offset=16, y_offset=16) as progress_corner:
                with ui.card().classes("studio-card w-80 max-w-[calc(100vw-2rem)] shadow-lg"):
                    progress_label = ui.label("").classes("font-medium")
                    progress_bar = ui.linear_progress(value=0).classes("w-full")
            progress_corner.set_visibility(False)
            progress_timer = ui.timer(0.2, redraw_progress, active=False)
            draft_settings = {
                "origin": origin,
                "input_model": input_model,
                "expected_voices": expected_voices,
                "recover_missing": recover_missing,
                "separate_vocals": separate_vocals,
                "destination": destination,
                "translation_provider": translation_provider,
                "sync_translation": sync_translation,
                "output_model": output_model,
                "treat_audio": treat_audio,
                "background": background,
                "voice_volume": voice_volume,
                "auto_speed": auto_speed,
                "auto_fit": auto_fit,
                "treat_human": treat_human,
                "latency": latency,
            }
            restore_draft()
            for control in draft_settings.values():
                control.on_value_change(lambda: save_draft())
            switch_mode(initial_human)
