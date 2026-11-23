"""NiceGUI desktop-browser studio. Run with f5-tts_studio."""

import argparse
import json
from pathlib import Path
from threading import Event

from nicegui import app, run, ui

from f5_tts.audio import local_audio_input
from f5_tts.studio import chatterbox_backend as chatterbox
from f5_tts.studio.ai_console import creative_consoles
from f5_tts.studio.audio_timeline import AudioTimeline
from f5_tts.studio.file_pages import register_file_pages
from f5_tts.studio.gemini_tips import STUDIO_FEATURES, gemini_tips, training_context_json
from f5_tts.studio.http_security import StudioBrowserBoundary
from f5_tts.studio.i18n import INTERFACE_LANGUAGES
from f5_tts.studio.local_pages import register_local_pages
from f5_tts.studio.microphone import Microphone
from f5_tts.studio.model_pages import register_model_pages
from f5_tts.studio.music_preview import music_preview
from f5_tts.studio.montage_page import EDITOR_PATH, register_montage_page, send_to_video_editor
from f5_tts.studio.reference_turns import reference_turn_editor
from f5_tts.studio.script_review import local_review_models, preferred_review_model
from f5_tts.studio.service import StudioService
from f5_tts.studio.story_pages import register_story_pages
from f5_tts.studio.training_pages import register_training_pages
from f5_tts.studio.video_dubbing_page import register_video_dubbing_page
from f5_tts.studio.youtube_studio import register_youtube_studio


CSS = """
body { background: #f3f6f8; color: #172c37; font-family: Inter, system-ui, sans-serif; }
.q-drawer { background: #102e38; color: #d2e5e8; }
.studio-card { background: white; border: 1px solid #e0e8ec; border-radius: 18px; box-shadow: none; padding: 24px; }
.studio-hero { background: linear-gradient(110deg,#113540,#176068); color: white; border-radius: 20px; padding: 32px; }
.studio-title { font-size: 30px; font-weight: 700; letter-spacing: -1px; }
.studio-muted { color: #627781; }
.studio-nav { width: 100%; justify-content: flex-start; padding: 12px 16px; border-radius: 10px; }
.studio-nav { color: #d6e8ea; margin: 3px 0; border-left: 3px solid transparent; transition: transform .2s ease, background .25s ease, border-color .25s ease, box-shadow .25s ease; animation: nav-enter .35s both; }
.studio-nav:hover { transform: translateX(5px); background: rgba(81,193,183,.13); border-left-color: #59c8ba; }
.studio-nav-active { background: linear-gradient(100deg,rgba(48,163,154,.28),rgba(48,163,154,.06)); border-left-color: #73ded1; box-shadow: inset 0 0 20px rgba(83,203,191,.08); }
.studio-tool-card { background: linear-gradient(145deg,#fff,#f1f8f8); transition: transform .22s ease, box-shadow .22s ease, border-color .22s ease; }
.studio-tool-card:hover { transform: translateY(-4px); border-color: #9bcac6; box-shadow: 0 12px 24px rgba(18,68,76,.10); }
.studio-brand { background: linear-gradient(145deg,#1b6269,#102e38 78%); border: 1px solid rgba(116,220,207,.28); border-radius: 18px; padding: 16px; box-shadow: 0 12px 30px rgba(3,18,24,.28); animation: brand-glow 5s ease-in-out infinite; }
.studio-nav-group { color: #82aaa9; font-size: 10px; letter-spacing: .16em; font-weight: 700; padding: 14px 12px 5px; }
@keyframes nav-enter { from { opacity: 0; transform: translateX(-8px); } to { opacity: 1; transform: translateX(0); } }
@keyframes brand-glow { 0%,100% { box-shadow: 0 12px 30px rgba(3,18,24,.25); } 50% { box-shadow: 0 12px 34px rgba(43,181,167,.19); } }
@media(prefers-reduced-motion:reduce) { .studio-nav,.studio-brand,.studio-tool-card { animation:none; transition:none; } }
.q-stepper { box-shadow: none; border: 1px solid #e0e8ec; border-radius: 18px; width: 100%; }
.q-uploader { box-shadow: none; border: 1px dashed #75a9ad; width: 100%; }
.q-field { width: 100%; }
@media(max-width:700px) { .studio-card,.studio-hero { padding: 18px; } .studio-title {font-size:25px;} }
"""


def register_pages(service):
    app.add_middleware(StudioBrowserBoundary)
    t = service.translate_ui
    app.on_shutdown(service.stop_training)

    async def action(fn, *args, **kwargs):
        try:
            return await run.io_bound(fn, *args, **kwargs)
        except chatterbox.SynthesisCancelled:
            return None
        except Exception as error:
            ui.notify(t(str(error)), type="negative", close_button=True, timeout=10000)
            return None

    def metric_card(title, initial, icon):
        with ui.card().classes("studio-tool-card p-3 gap-1").style("flex:1 1 180px;min-width:160px"):
            with ui.row().classes("items-center gap-2"):
                ui.icon(icon, color="primary", size="18px")
                ui.label(title).classes("text-xs font-semibold uppercase tracking-wide")
            return ui.label(initial).classes("text-sm font-medium break-words")

    def shell(section, *, local_navigation=None):
        ui.context.client.page.language = service.interface_language()
        ui.colors(primary="#137d80", secondary="#294d5b", accent="#2bb5a7")
        ui.add_css(CSS)
        with ui.left_drawer(value=True).props("show-if-above bordered").classes("p-4") as drawer:
            with ui.column().classes("studio-brand w-full gap-1 mb-5"):
                with ui.row().classes("items-center gap-3"):
                    ui.icon("graphic_eq", size="30px").classes("text-teal-200")
                    ui.label(t("F5 / STUDIO")).classes("text-xl font-bold")
                ui.label(t("Seu espaço de voz")).classes("text-sm opacity-75")
            groups = [
                (
                    "CRIAR E EDITAR",
                    [
                        ("Criar áudio", "graphic_eq", "/"),
                        ("Regravar áudio", "mic", "/revoice"),
                        ("Ferramentas de áudio", "equalizer", "/audio-tools"),
                    ],
                ),
                (
                    "DUBLAGEM",
                    [
                        ("Dublagem por IA", "movie", "/video-dubbing"),
                        ("Dublagem convencional com humanos", "mic", "/video-dubbing-human"),
                    ],
                ),
                (
                    "EDIÇÃO",
                    [
                        ("Editor de vídeo avançado", "video_library", EDITOR_PATH),
                        ("Estúdio YouTube", "smart_display", "/youtube-studio"),
                    ],
                ),
                (
                    "PROJETOS",
                    [
                        ("Histórias e personagens", "auto_stories", "/stories"),
                        ("Projetos de narração", "article", "/projects"),
                        ("Transcrição e legendas", "subtitles", "/transcription"),
                    ],
                ),
                (
                    "BIBLIOTECA",
                    [
                        ("Biblioteca de áudios", "folder_managed", "/files"),
                        ("Minhas vozes", "record_voice_over", "/voices"),
                        ("Meus modelos", "inventory_2", "/models"),
                        ("Histórico", "history", "/history"),
                    ],
                ),
                ("SISTEMA", [("Treinar uma voz", "school", "/training"), ("Configurações", "tune", "/settings")]),
            ]
            active_sections = {"Regravar com outra voz": "Regravar áudio", "Dublagem de vídeo": "Dublagem por IA"}

            def navigate(target):
                if local_navigation and target in local_navigation:
                    return local_navigation[target]()
                return ui.navigate.to(target)

            for group_name, items in groups:
                ui.label(t(group_name)).classes("studio-nav-group")
                for title, icon, route in items:
                    active = title == active_sections.get(section, section)
                    button = (
                        ui.button(t(title), icon=icon, on_click=lambda target=route: navigate(target))
                        .props("flat no-caps")
                        .classes("studio-nav" + (" studio-nav-active" if active else ""))
                    )
                    button.style(f"animation-delay:{items.index((title, icon, route)) * 35}ms")
            ui.space()
            ui.label(t("EXECUÇÃO LOCAL")).classes("text-xs tracking-widest opacity-60")
            ui.label(t("Os modelos são carregados quando você precisa deles.")).classes("text-sm opacity-75")
        with ui.header().classes("bg-white text-slate-700 border-b items-center"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat round")
            ui.label(t(section)).classes("font-semibold")
            ui.space()

            async def change_language(event):
                if event.value != service.interface_language():
                    service.set_interface_language(event.value)
                    ui.navigate.reload()

            ui.select(
                INTERFACE_LANGUAGES,
                value=service.interface_language(),
                label=t("Idioma da interface"),
                on_change=change_language,
            ).props("outlined dense").style("width:200px;max-width:100%")
            ui.badge(t("Workspace local"), color="teal-1", text_color="teal-9")
        return ui.column().classes("w-full max-w-6xl mx-auto p-4 md:p-8 gap-6")

    async def receive(event, paths, label, on_reference=None):
        try:
            if event.file.size() > 100_000_000:
                raise ValueError("Arquivo recusado. O limite é 100 MB por arquivo.")
            path = service.upload_path(event.file.name)
            try:
                await event.file.save(path)
                await run.io_bound(local_audio_input, path)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            paths.append(str(path))
            label.set_text(t("{value_0} arquivo(s) enviado(s).", value_0=len(paths)))
            if on_reference:
                await on_reference()
        except Exception as error:
            ui.notify(t(str(error)), type="negative")

    def audio_upload(paths, label, multiple=False, reference=False, on_reference=None):
        if reference:

            async def receive_recording(event):
                result = await action(service.microphone_reference, event.args)
                if result:
                    paths.append(result)
                    if on_reference:
                        await on_reference()
                    label.set_text(t("Gravação do microfone selecionada como referência."))
                    ui.notify(t("Referência pronta para usar."), type="positive")
                recorder.run_method("finish", result is not None)

            recorder = Microphone(receive_recording, t).classes("w-full")
            ui.label(t("Ou envie um áudio pronto:")).classes("studio-muted text-sm")
        ui.upload(
            label=t("Arraste os áudios ou clique para escolher"),
            multiple=multiple,
            auto_upload=True,
            max_file_size=100_000_000,
            max_files=100 if multiple else 1,
            on_upload=lambda event: receive(event, paths, label, on_reference),
            on_rejected=lambda: ui.notify(t("Arquivo recusado. O limite é 100 MB por arquivo."), type="warning"),
        ).props('accept=".wav,.mp3,.flac,.ogg,.m4a,.aac"').classes("w-full")

    register_local_pages(service, shell, action, audio_upload)
    register_video_dubbing_page(service, shell, action)
    register_montage_page(service, shell, action)
    register_youtube_studio(service, shell, action)
    register_model_pages(service, shell, action)
    register_story_pages(service, shell, action)
    register_training_pages(service, shell, action, audio_upload)
    register_file_pages(service, shell, action)

    @ui.page("/")
    def create_audio():
        paths, generated = [], {}
        with shell("Criar áudio"):
            with ui.column().classes("studio-hero w-full gap-2"):
                ui.label(t("DÊ VOZ ÀS SUAS IDEIAS")).classes("text-xs tracking-widest opacity-75")
                ui.label(t("Transforme texto em voz.")).classes("studio-title")
                ui.label(t("Escolha uma referência, escreva seu roteiro e ouça o resultado.")).classes("opacity-85")
            with ui.row().classes("w-full gap-6 items-start flex-wrap md:flex-nowrap"):
                with ui.card().classes("studio-card w-full md:w-2/3 gap-4"):
                    ui.label(t("01 / A voz")).classes("font-semibold text-lg")
                    saved_voices = {item["id"]: item for item in service.read()["voices"]}
                    voices = {identifier: item["name"] for identifier, item in saved_voices.items()}
                    recent = {item["path"]: item for item in service.recent_references()}
                    with ui.card().classes("w-full gap-3 studio-tool-card"):
                        ui.label(t("Minhas referências")).classes("font-semibold")
                        voice = ui.select(voices, label=t("Referência favorita"), clearable=True).props("outlined")
                        recent_selector = ui.select(
                            {path: f"{item['name']} · {item['recorded_at']}" for path, item in recent.items()},
                            label=t("Gravações e referências recentes"),
                            clearable=True,
                        ).props("outlined")
                        with ui.row().classes("w-full items-end gap-2"):
                            favorite_name = ui.input(t("Nome para salvar a referência atual")).props("outlined")
                            favorite_button = ui.button(t("Salvar como favorita"), icon="bookmark_add").props(
                                "outline no-caps"
                            )
                            favorite_button.disable()

                        async def refresh_recent():
                            latest = await run.io_bound(service.recent_references)
                            recent.clear()
                            recent.update({item["path"]: item for item in latest})
                            recent_selector.set_options(
                                {path: f"{item['name']} · {item['recorded_at']}" for path, item in recent.items()}
                            )

                        async def choose_recent(event):
                            path = event.value
                            if path in recent:
                                paths.append(path)
                                if voice.value:
                                    voice.set_value(None)
                                else:
                                    await refresh_reference()

                        recent_selector.on_value_change(choose_recent)

                        async def save_favorite():
                            source = active_reference()
                            if not source:
                                return
                            favorite_button.disable()
                            try:
                                result = await action(
                                    service.save_voice, favorite_name.value, source, reference_text.value
                                )
                                if result:
                                    saved_voices[result["id"]] = result
                                    voices[result["id"]] = result["name"]
                                    voice.set_options(voices)
                                    voice.set_value(result["id"])
                                    favorite_name.set_value("")
                                    recent_selector.set_value(None)
                                    await refresh_recent()
                                    ui.notify(t("Referência salva nas favoritas."), type="positive")
                            finally:
                                favorite_button.set_enabled(bool(active_reference()))

                        favorite_button.on_click(save_favorite)

                    ui.label(
                        t("Grave pelo microfone ou envie uma referência curta. Prefira até 12 segundos, sem música.")
                    ).classes("studio-muted")
                    upload_status = ui.label(t("Nenhum arquivo enviado.")).classes("text-sm")

                    def active_reference():
                        if voice.value:
                            return saved_voices[voice.value]["reference"]
                        return paths[-1] if paths else ""

                    async def select_upload():
                        recent_selector.set_value(None)
                        if paths:
                            await action(service.remember_reference, paths[-1])
                            await refresh_recent()
                        if voice.value is not None:
                            voice.set_value(None)
                        else:
                            await refresh_reference()

                    audio_upload(paths, upload_status, reference=True, on_reference=select_upload)
                    reference_text = ui.textarea(
                        label=t("O que é dito na referência? (opcional)"),
                        placeholder=t("Vazio: transcrição automática."),
                    ).props("outlined rows=2")

                    async def transcribe_reference():
                        source = active_reference()
                        previous_text = reference_text.value
                        transcribe_button.disable()
                        try:
                            result = await action(service.transcribe_reference, source)
                            if result and active_reference() == source and reference_text.value == previous_text:
                                reference_text.set_value(result)
                        finally:
                            transcribe_button.set_enabled(bool(active_reference()))

                    transcribe_button = ui.button(
                        t("Transcrever referência"), icon="subtitles", on_click=transcribe_reference
                    ).props("outline no-caps")
                    transcribe_button.disable()

                    with ui.column().classes("w-full gap-1") as reference_quality:
                        ui.label(t("Qualidade da referência")).classes("font-semibold")
                        quality_summary = ui.label("").classes("text-sm studio-muted")
                        quality_tips = ui.column().classes("w-full gap-1")
                    reference_quality.set_visibility(False)

                    async def collect_tips(topic):
                        if topic == "script":
                            script = (text.value or "").strip()
                            if not script:
                                raise ValueError("Escreva ou cole um roteiro no campo de narração primeiro.")
                            return script if len(script) <= 16_000 else "Início do roteiro:\n" + script[:16_000]
                        if topic == "reference":
                            source = active_reference()
                            if not source:
                                raise ValueError("Selecione uma referência para pedir dicas sobre o áudio.")
                            measured = await run.io_bound(service.audio_waveform, source, 180, True)
                            return json.dumps({name: value for name, value in measured.items() if name != "peaks"})
                        if topic == "training":
                            return await training_context_json(service, saved_model.value, trained_project.value)
                        return STUDIO_FEATURES

                    refresh_gemini_tips = gemini_tips(service, collect_tips)
                    gemini_reference_enabled = ui.switch(t("Ativar análise da referência com Gemini"), value=False)
                    with ui.column().classes("w-full gap-2") as gemini_reference_panel:
                        ui.label(
                            t(
                                "Ao analisar com Gemini, este áudio será enviado ao Google. A análise local continua disponível."
                            )
                        ).classes("text-sm studio-muted")
                        gemini_assessment = ui.column().classes("w-full gap-1")
                        gemini_reference_status = ui.label("").classes("text-sm studio-muted")

                        async def analyze_gemini():
                            source = active_reference()
                            if not gemini_reference_enabled.value or not source:
                                return
                            model = (
                                cloud_model.value if provider.value == "gemini" else session_models.get("gemini", "")
                            )
                            gemini_reference_button.disable()
                            gemini_assessment.clear()
                            gemini_reference_status.set_text(t("Analisando referência com Gemini…"))
                            try:
                                result = await action(service.analyze_reference_with_gemini, source, model=model)
                                if not result or active_reference() != source or not gemini_reference_enabled.value:
                                    return
                                with gemini_assessment:
                                    ui.label(result["assessment"]).classes("font-medium")
                                    for tip in result["tips"]:
                                        ui.label(tip).classes("text-sm studio-muted")
                                    if result["transcript"]:
                                        suggested_transcript = (
                                            ui.textarea(
                                                t("Transcrição sugerida pelo Gemini"), value=result["transcript"]
                                            )
                                            .props("outlined rows=3")
                                            .classes("w-full")
                                        )

                                        def use_gemini_transcript():
                                            if active_reference() == source and gemini_reference_enabled.value:
                                                reference_text.set_value(suggested_transcript.value)

                                        ui.button(
                                            t("Usar transcrição do Gemini"), on_click=use_gemini_transcript
                                        ).props("flat no-caps")
                            finally:
                                gemini_reference_status.set_text("")
                                gemini_reference_button.set_enabled(bool(active_reference()))

                        gemini_reference_button = ui.button(
                            t("Analisar e transcrever com Gemini"), icon="hearing", on_click=analyze_gemini
                        ).props("outline no-caps")
                        gemini_reference_button.disable()
                        ui.label(
                            t("Use a chave Gemini configurada na revisão do roteiro ou em GEMINI_API_KEY.")
                        ).classes("text-sm studio-muted")
                    gemini_reference_panel.bind_visibility_from(gemini_reference_enabled, "value")

                    reference_editor = {"source": ""}
                    with ui.expansion(t("Ouvir e recortar a referência"), icon="content_cut").classes("w-full"):
                        reference_hint = ui.label(t("Selecione uma referência para escolher o trecho.")).classes(
                            "text-sm studio-muted"
                        )

                        async def use_clip(event):
                            source = active_reference()
                            selection = event.args
                            if (
                                not source
                                or reference_editor["source"] != source
                                or reference_editor.get("trimming")
                                or not reference_audio.matches_trim(selection)
                            ):
                                return
                            reference_editor["trimming"] = True
                            reference_audio.set_trimming(True)
                            try:
                                result = await action(
                                    service.trim_reference, source, selection.get("start"), selection.get("end")
                                )
                                if result and active_reference() == source:
                                    paths.append(result["path"])
                                    await select_upload()
                                    upload_status.set_text(t("Recorte selecionado como referência."))
                            finally:
                                reference_editor["trimming"] = False
                                reference_audio.set_trimming(False)

                        reference_audio = AudioTimeline(t, on_trim=use_clip).classes("w-full")

                    async def refresh_reference():
                        source = active_reference()
                        reference_editor["source"] = ""
                        gemini_assessment.clear()
                        gemini_reference_button.set_enabled(bool(source))
                        saved = saved_voices.get(voice.value, {})
                        reference_text.set_value(saved.get("transcript", recent.get(source, {}).get("transcript", "")))
                        favorite_button.set_enabled(bool(source))
                        reference_audio.set_source(source)
                        reference_hint.set_visibility(not source)
                        reference_quality.set_visibility(False)
                        transcribe_button.set_enabled(bool(source))
                        if not source:
                            return
                        waveform = await action(service.audio_waveform, source, 180, True)
                        if waveform and active_reference() == source:
                            duration = waveform["duration"]
                            quality = waveform["quality"]
                            quality_summary.set_text(
                                t(
                                    "{duration} s · {rate} Hz · RMS {rms} dBFS · clipping {clipping}%",
                                    duration=f"{duration:.2f}",
                                    rate=waveform["sample_rate"],
                                    rms=f"{quality['rms_db']:.1f}",
                                    clipping=f"{quality['clipping']:.2f}",
                                )
                            )
                            tips = {
                                "too_short": t("Grave pelo menos um segundo de fala contínua."),
                                "too_long": t("Recorte uma frase completa. Prefira uma referência de até 12 segundos."),
                                "quiet": t("O volume está baixo. Aproxime o microfone e grave com volume natural."),
                                "clipping": t("Há sinais de saturação. Reduza o ganho do microfone e grave novamente."),
                                "silence": t(
                                    "Há muitos trechos silenciosos. Recorte a fala e mantenha as pausas naturais."
                                ),
                                "sample_rate": t(
                                    "A taxa de amostragem é baixa. Se possível, regrave em 24 kHz ou mais."
                                ),
                            }
                            quality_tips.clear()
                            with quality_tips:
                                for check in quality["checks"]:
                                    ui.label(tips[check]).classes("text-sm text-orange-900")
                                if not quality["checks"]:
                                    ui.label(t("Duração e nível adequados para testar no F5-TTS.")).classes(
                                        "text-sm text-teal-900"
                                    )
                                ui.label(
                                    t("Ouça e confira se há uma só voz, sem música, eco ou ruído de fundo.")
                                ).classes("text-sm studio-muted")
                            reference_quality.set_visibility(True)
                            reference_editor["source"] = source
                            reference_audio.set_waveform(waveform)

                    voice.on_value_change(refresh_reference)
                    saved_model = ui.select(
                        service.model_choices(),
                        value="original",
                        label=t("Modelo para gerar áudio"),
                    ).props("outlined")
                    chatterbox_language = ui.select(
                        chatterbox.LANGUAGES,
                        value=chatterbox.DEFAULT_LANGUAGE,
                        label=t("Idioma do Chatterbox"),
                    ).props("outlined")
                    chatterbox_language.set_visibility(False)
                    saved_model.on_value_change(
                        lambda event: chatterbox_language.set_visibility(event.value == chatterbox.MODEL_ID)
                    )
                    speech_speed = ui.number(
                        t("Ritmo da fala (F5-TTS)"),
                        value=service.read()["settings"].get("speed", 1.0),
                        min=0.5,
                        max=2,
                        step=0.05,
                    ).props("outlined")
                    speech_speed.set_visibility(saved_model.value != chatterbox.MODEL_ID)
                    saved_model.on_value_change(
                        lambda event: speech_speed.set_visibility(event.value != chatterbox.MODEL_ID)
                    )
                    ui.link(t("Cadastrar meu modelo em português ou outro checkpoint"), "/models").classes("text-sm")
                    with ui.expansion(t("Usar um modelo que eu treinei"), icon="school").classes("w-full"):
                        trained_project = ui.select(
                            service.projects(), label=t("Projeto treinado (opcional)"), clearable=True
                        ).props("outlined")
                        trained_checkpoint = ui.select({}, label=t("Checkpoint para usar")).props("outlined")

                        async def choose_model():
                            if trained_project.value:
                                saved_model.set_value("original")
                            trained_checkpoint.set_value(None)
                            if not trained_project.value:
                                trained_checkpoint.set_options({})
                                return
                            models = await action(service.checkpoints, trained_project.value)
                            if models is not None:
                                trained_checkpoint.set_options({model: Path(model).name for model in models})
                                if not models:
                                    ui.notify(
                                        t("O projeto ainda não tem checkpoints. Conclua um treino primeiro."),
                                        type="info",
                                    )

                        trained_project.on_value_change(choose_model)
                        saved_model.on_value_change(
                            lambda: trained_project.set_value(None) if saved_model.value != "original" else None
                        )
                        ui.label(t("Sem projeto selecionado, é usado o modelo escolhido acima.")).classes(
                            "studio-muted text-sm"
                        )
                    ui.separator()
                    ui.label(t("02 / O roteiro")).classes("font-semibold text-lg")
                    text = ui.textarea(
                        label=t("Texto para narrar"), placeholder=t("Digite ou cole seu texto aqui…")
                    ).props("outlined rows=7")
                    review_state = {"busy": False, "reviewed": None, "pending": None}
                    with ui.expansion(t("Revisar roteiro para F5-TTS"), icon="edit_note").classes(
                        "w-full"
                    ) as review_panel:
                        provider = ui.select(
                            {
                                "gemini": "Gemini",
                                "openai": "OpenAI",
                                "anthropic": "Claude",
                                "ollama": t("Ollama local"),
                            },
                            label=t("Provedor de revisão"),
                            value="gemini",
                        ).props("outlined")
                        with ui.column().classes("w-full") as cloud_settings:
                            ui.label(
                                t("APIs externas recebem o roteiro. O áudio de referência fica no Studio.")
                            ).classes("text-sm studio-muted")
                            key_links = {
                                "gemini": "https://aistudio.google.com/apikey",
                                "openai": "https://platform.openai.com/api-keys",
                                "anthropic": "https://platform.claude.com/settings/keys",
                            }
                            key_link = ui.link(t("Obter chave da API"), key_links["gemini"], new_tab=True)
                            cloud_model = ui.input(
                                t("Modelo de revisão"), value=preferred_review_model("gemini")
                            ).props("outlined")
                            session_models = {}
                            current_provider = "gemini"

                            def refresh_key_form(editing=False):
                                active = service.review_key_active(current_provider)
                                api_key.set_value("")
                                key_status.set_visibility(active)
                                change_key.set_visibility(active and not editing)
                                key_form.set_visibility(editing or not active)
                                cancel_key.set_visibility(active)

                            def save_key():
                                try:
                                    service.save_review_key(current_provider, api_key.value)
                                except ValueError as error:
                                    ui.notify(t(str(error)), type="warning")
                                except OSError:
                                    ui.notify(t("Não foi possível salvar a chave da API."), type="negative")
                                else:
                                    refresh_key_form()
                                    refresh_console_status()
                                    refresh_gemini_tips()

                            with ui.row().classes("items-center gap-2"):
                                key_status = ui.label(t("Ativo")).classes("text-sm font-semibold text-positive")
                                change_key = ui.button(
                                    t("Trocar chave"), on_click=lambda: refresh_key_form(True)
                                ).props("flat no-caps")
                            with ui.column().classes("w-full") as key_form:
                                api_key = ui.input(t("Chave da API"), password=True).props("outlined autocomplete=off")
                                with ui.row().classes("items-center gap-2"):
                                    ui.button(t("Salvar chave"), on_click=save_key).props("outline no-caps")
                                    cancel_key = ui.button(t("Cancelar"), on_click=lambda: refresh_key_form()).props(
                                        "flat no-caps"
                                    )
                            refresh_key_form()

                        with ui.column().classes("w-full") as local_settings:
                            ui.label(
                                t("Inicie o Ollama nesta máquina e instale um modelo para revisar sem API externa.")
                            ).classes("text-sm studio-muted")
                            local_model = ui.select([], label=t("Modelo local instalado")).props("outlined")
                            ui.link("Ollama", "https://ollama.com/download", new_tab=True)

                            async def load_local_models():
                                available = await action(local_review_models)
                                if available is not None:
                                    selected = local_model.value or preferred_review_model("ollama")
                                    local_model.set_options(available)
                                    local_model.set_value(
                                        selected if selected in available else next(iter(available), None)
                                    )
                                    if not available:
                                        ui.notify(t("Instale um modelo no Ollama e atualize a lista."), type="warning")

                            ui.button(t("Buscar modelos locais"), icon="refresh", on_click=load_local_models).props(
                                "flat no-caps"
                            )
                        local_settings.set_visibility(False)
                        extra_instructions = (
                            ui.textarea(
                                t("O que você quer mudar ou acrescentar?"),
                                placeholder=t("Exemplo: adicione uma abertura breve e deixe as falas mais naturais."),
                            )
                            .props("outlined rows=3")
                            .classes("w-full")
                        )
                        delivery_style = ui.select(
                            {
                                "natural": t("Natural e conversacional"),
                                "documentary": t("Documentário calmo"),
                                "educational": t("Educativo envolvente"),
                                "energetic": t("Energético e direto"),
                                "story": t("Narrativa dramática contida"),
                            },
                            value="natural",
                            label=t("Intenção da narração"),
                        ).props("outlined")
                        automatic_review = ui.switch(t("Sugerir revisão antes de gerar áudio"), value=False)
                        review_status = ui.label("").classes("text-sm studio-muted")

                        def review_identity():
                            local = provider.value == "ollama"
                            model = local_model.value if local else cloud_model.value
                            return (
                                (text.value or "").strip(),
                                provider.value,
                                (model or "").strip(),
                                (extra_instructions.value or "").strip(),
                                delivery_style.value,
                            )

                        def switch_provider():
                            nonlocal current_provider
                            if current_provider != "ollama":
                                session_models[current_provider] = cloud_model.value
                            current_provider = provider.value
                            local = current_provider == "ollama"
                            cloud_settings.set_visibility(not local)
                            local_settings.set_visibility(local)
                            if not local:
                                key_link.props(f'href="{key_links[current_provider]}"')
                                cloud_model.set_value(
                                    session_models.get(current_provider) or preferred_review_model(current_provider)
                                )
                                refresh_key_form()
                            else:
                                api_key.set_value("")

                        provider.on_value_change(switch_provider)

                        async def review_script():
                            if review_state["busy"]:
                                return False
                            identity = review_identity()
                            source, selected_provider, model, instructions, style = identity
                            style_instructions = {
                                "natural": "Use uma entrega natural e conversacional, com frases fáceis de pronunciar e pausas sugeridas pela pontuação.",
                                "documentary": "Use uma entrega calma de documentário, com pausas claras e frases equilibradas, sem dramatização exagerada.",
                                "educational": "Use uma entrega educativa envolvente, articulando termos importantes e variando o ritmo para facilitar a compreensão.",
                                "energetic": "Use uma entrega energética e direta, com frases curtas e ênfase textual moderada, sem alterar fatos.",
                                "story": "Use uma entrega narrativa expressiva e contida, com pausas naturais e sem acrescentar acontecimentos ou instruções ao texto falado.",
                            }[style]
                            instructions = "\n".join(filter(None, (style_instructions, instructions)))
                            review_state["busy"] = True
                            review_state["pending"] = None
                            suggestion.set_visibility(False)
                            suggest_button.disable()
                            review_status.set_text(t("Preparando sugestão para o F5-TTS…"))
                            try:
                                result = await action(
                                    service.suggest_narration,
                                    source,
                                    provider=selected_provider,
                                    model=model,
                                    instructions=instructions,
                                )
                            finally:
                                review_state["busy"] = False
                                suggest_button.enable()
                                review_status.set_text("")
                            if not result or review_identity() != identity:
                                return False
                            if result == source:
                                review_state["reviewed"] = identity
                                review_status.set_text(t("A revisão manteve o roteiro original."))
                                return True
                            review_state["pending"] = identity
                            revised_text.set_value(result)
                            suggestion.set_visibility(True)
                            review_panel.open()
                            return False

                        suggest_button = ui.button(
                            t("Sugerir melhorias"), icon="auto_fix_high", on_click=review_script
                        ).props("outline no-caps")
                        music_library_refresh = []
                        refresh_console_status = creative_consoles(
                            service,
                            action,
                            text,
                            lambda value: generate_from_console(value),
                            music_library_refresh,
                        )

                    with ui.column().classes("w-full gap-2") as suggestion:
                        revised_text = ui.textarea(t("Roteiro sugerido")).props("outlined rows=5").classes("w-full")

                        def finish_review(apply_suggestion):
                            current = review_identity()
                            if current != review_state["pending"]:
                                ui.notify(t("O roteiro ou o modelo mudou. Solicite uma nova sugestão."), type="warning")
                            else:
                                revised = (revised_text.value or "").strip()
                                if apply_suggestion:
                                    if not revised:
                                        return
                                    text.set_value(revised)
                                    current = (revised, *current[1:])
                                review_state["reviewed"] = current
                            review_state["pending"] = None
                            suggestion.set_visibility(False)

                        with ui.row():
                            ui.button(t("Manter original"), on_click=lambda: finish_review(False)).props("flat no-caps")
                            ui.button(t("Usar sugestão no roteiro"), on_click=lambda: finish_review(True)).props(
                                "no-caps"
                            )
                    suggestion.set_visibility(False)

                    reference_turns_enabled, selected_reference_turns = reference_turn_editor(
                        t,
                        text,
                        voices,
                        audio_upload,
                        lambda: {
                            "reference": paths[-1] if paths else None,
                            "voice_id": voice.value,
                            "transcript": reference_text.value,
                        },
                    )

                    ui.label(t("03 / Saída do áudio")).classes("font-semibold text-lg")
                    with ui.row().classes("w-full items-start gap-4 flex-wrap"):
                        output_format = (
                            ui.select({"wav": "WAV", "mp3": "MP3"}, value="wav", label=t("Formato de saída"))
                            .props("outlined")
                            .style("flex:1 1 150px")
                        )
                        output_quality = (
                            ui.select(
                                {
                                    "high": t("Alta qualidade"),
                                    "standard": t("Padrão"),
                                    "compact": t("Arquivo compacto"),
                                },
                                value="standard",
                                label=t("Qualidade do arquivo"),
                            )
                            .props("outlined")
                            .style("flex:1 1 200px")
                        )
                    quality_help = ui.label("").classes("text-sm studio-muted")

                    def explain_quality():
                        if output_format.value == "mp3":
                            rates = {"high": "320", "standard": "192", "compact": "128"}
                            quality_help.set_text(
                                t(
                                    "MP3: {value_0} kbps; menor bitrate gera arquivos menores.",
                                    value_0=rates[output_quality.value],
                                )
                            )
                        else:
                            descriptions = {
                                "high": t("WAV PCM 24-bit · 24 kHz · maior fidelidade e arquivo maior."),
                                "standard": t("WAV PCM 16-bit · 24 kHz · equilíbrio recomendado."),
                                "compact": t("WAV PCM 16-bit · 16 kHz · arquivo menor para fala."),
                            }
                            quality_help.set_text(descriptions[output_quality.value])

                    output_format.on_value_change(explain_quality)
                    output_quality.on_value_change(explain_quality)
                    explain_quality()
                    with ui.row().classes("w-full gap-5 flex-wrap"):
                        remove_noise = ui.checkbox(t("Reduzir ruído constante no áudio"), value=False)
                        stereo = ui.checkbox(t("Exportar em estéreo"), value=False)
                    ui.label(
                        t(
                            "O estéreo duplica a voz mono nos dois canais. A redução de ruído pode alterar o timbre; use-a só se notar chiado constante."
                        )
                    ).classes("text-xs studio-muted")
                    status = ui.label(t("Pronto para começar.")).classes("studio-muted")
                    cancel_event = None

                    def cancel_synthesis():
                        if cancel_event is not None:
                            cancel_event.set()
                            cancel_button.disable()
                            status.set_text(t("Cancelando síntese…"))

                    async def generate():
                        nonlocal cancel_event
                        button.disable()
                        try:
                            if automatic_review.value and review_state["reviewed"] != review_identity():
                                if review_state["pending"] == review_identity() or not await review_script():
                                    return
                            status.set_text(t("Gerando… No primeiro uso, os modelos serão baixados. Aguarde."))
                            settings = (
                                trained_project.value,
                                trained_checkpoint.value,
                                saved_model.value,
                                output_format.value,
                                output_quality.value,
                                remove_noise.value,
                                stereo.value,
                            )
                            cancel_event = Event()
                            cancel_button.enable()
                            cancel_button.set_visibility(True)
                            if reference_turns_enabled.value:
                                try:
                                    parts = selected_reference_turns()
                                except ValueError as error:
                                    status.set_text(t(str(error)))
                                    return
                                result = await action(
                                    service.generate_reference_turns,
                                    parts,
                                    *settings,
                                    language=chatterbox_language.value,
                                    cancel_event=cancel_event,
                                )
                            else:
                                result = await action(
                                    service.generate,
                                    text.value,
                                    paths[-1] if paths else None,
                                    reference_text.value,
                                    voice.value,
                                    *settings,
                                    language=chatterbox_language.value,
                                    speech_speed=speech_speed.value,
                                    cancel_event=cancel_event,
                                )
                            if result:
                                await refresh_recent()
                                generated.update(result)
                                await player.load(result["path"], service.audio_waveform)
                                await preview_narration(result["path"])
                                download.enable()
                                send_audio.enable()
                                verify_button.enable()
                                status.set_text(
                                    t(
                                        "Áudio {value_0} pronto. Você também pode encontrá-lo no Histórico.",
                                        value_0=output_format.value.upper(),
                                    )
                                )
                            elif cancel_event.is_set():
                                status.set_text(t("Síntese cancelada."))
                            else:
                                status.set_text(t("Geração não concluída. Confira a mensagem e tente novamente."))
                        finally:
                            cancel_event = None
                            cancel_button.set_visibility(False)
                            button.enable()

                    async def generate_from_console(value):
                        if not button.enabled:
                            ui.notify(t("Aguarde a geração atual."), type="warning")
                            return
                        text.set_value(value)
                        await generate()

                    button = (
                        ui.button(t("Gerar áudio"), icon="play_arrow", on_click=generate)
                        .props("no-caps unelevated")
                        .classes("w-full py-3")
                    )
                    cancel_button = (
                        ui.button(t("Cancelar síntese"), icon="stop", on_click=cancel_synthesis)
                        .props("outline no-caps color=negative")
                        .classes("w-full")
                    )
                    cancel_button.set_visibility(False)
                with ui.column().classes("w-full md:w-1/3 gap-5"):
                    with ui.card().classes("studio-card w-full"):
                        ui.label(t("04 / Resultado")).classes("font-semibold text-lg")
                        player = AudioTimeline(t).classes("w-full")
                        with ui.expansion(t("Conferir pronúncia e texto com Whisper"), icon="fact_check").classes(
                            "w-full"
                        ):
                            verification_status = ui.label(
                                t("Gere um áudio para conferir o que foi pronunciado.")
                            ).classes("studio-muted")
                            verification = (
                                ui.textarea(t("Transcrição do áudio gerado")).props("outlined rows=3").classes("w-full")
                            )

                            async def verify_generated_audio():
                                if not generated.get("path"):
                                    return
                                verify_button.disable()
                                verification_status.set_text(t("Transcrevendo a narração gerada localmente…"))
                                try:
                                    transcript = await action(service.transcribe_reference, generated["path"])
                                    if transcript is not None:
                                        verification.set_value(transcript)
                                        def normalize(value):
                                            return " ".join(value.casefold().split()).strip(" .,!?;:")

                                        if normalize(transcript) == normalize(generated.get("text", "")):
                                            verification_status.set_text(
                                                t(
                                                    "A transcrição coincide com o roteiro. Confira também a pronúncia ouvindo o áudio."
                                                )
                                            )
                                        else:
                                            verification_status.set_text(
                                                t(
                                                    "Há diferenças entre o roteiro e a fala reconhecida. Revise o trecho ouvindo o áudio."
                                                )
                                            )
                                finally:
                                    verify_button.enable()

                            verify_button = ui.button(
                                t("Transcrever e comparar"), icon="spellcheck", on_click=verify_generated_audio
                            ).props("outline")
                            verify_button.disable()
                        download = ui.button(
                            t("Baixar áudio"), icon="download", on_click=lambda: ui.download.file(generated["path"])
                        ).props("flat no-caps")
                        download.disable()
                        send_audio = ui.button(
                            t("Enviar ao editor de vídeo"),
                            icon="video_library",
                            on_click=lambda: send_to_video_editor(
                                service,
                                action,
                                [(generated["path"], generated.get("text", "")[:80])],
                                generated.get("text", "")[:70] or t("Áudio gerado"),
                            ),
                        ).props("outline no-caps")
                        send_audio.disable()
                        preview_narration = music_preview(service, t, action)
                        preview_narration.register_refresh(music_library_refresh)
                        ui.separator()
                        ui.label(t("Progresso da geração")).classes("font-semibold")
                        generation_message = ui.label(t("Aguardando geração.")).classes("text-sm studio-muted")
                        generation_bar = ui.linear_progress(value=0, show_value=False).props("rounded")
                        with ui.row().classes("w-full gap-2 flex-wrap"):
                            cpu_metric = metric_card(t("CPU"), t("Aguardando dados"), "memory")
                            gpu_metric = metric_card(t("GPU"), t("Aguardando dados"), "developer_board")
                            ram_metric = metric_card(t("Memória RAM"), t("Aguardando dados"), "database")
                            disk_metric = metric_card(t("SSD / disco"), t("Aguardando dados"), "storage")

                        ui.label(
                            t(
                                "Uso do disco é do sistema inteiro; espaço livre corresponde à unidade onde o Studio guarda os arquivos."
                            )
                        ).classes("text-xs studio-muted")

                        async def update_generation_display():
                            progress = service.generation_status()
                            generation_bar.set_value(progress["percent"] / 100)
                            generation_message.set_text(
                                t(
                                    "{value_0} · {value_1}%",
                                    value_0=t(progress["message"]),
                                    value_1=f"{progress['percent']:.0f}",
                                )
                            )
                            if not progress["running"]:
                                return
                            metrics = await run.io_bound(service.system_metrics)
                            cpu_metric.set_text(t("{value_0}%", value_0=f"{metrics['cpu_percent']:.0f}"))
                            if metrics["gpu_vram_total_gb"]:
                                gpu_use = "—" if metrics["gpu_percent"] is None else f"{metrics['gpu_percent']}%"
                                gpu_metric.set_text(
                                    t(
                                        "{value_0} · {value_1} · VRAM {value_2}/{value_3} GB",
                                        value_0=metrics["gpu_name"],
                                        value_1=gpu_use,
                                        value_2=f"{metrics['gpu_vram_used_gb']:.1f}",
                                        value_3=f"{metrics['gpu_vram_total_gb']:.1f}",
                                    )
                                )
                            else:
                                gpu_metric.set_text(t("GPU não detectada; gerando pela CPU."))
                            ram_metric.set_text(
                                t(
                                    "{value_0}/{value_1} GB · {value_2}%",
                                    value_0=f"{metrics['ram_used_gb']:.1f}",
                                    value_1=f"{metrics['ram_total_gb']:.1f}",
                                    value_2=f"{metrics['ram_percent']:.0f}",
                                )
                            )
                            disk_metric.set_text(
                                t(
                                    "{value_0}% usado · {value_1} GB livre · ↓ {value_2} / ↑ {value_3} MB/s",
                                    value_0=f"{metrics['disk_used_percent']:.0f}",
                                    value_1=f"{metrics['disk_free_gb']:.1f}",
                                    value_2=f"{metrics['disk_read_mbps']:.1f}",
                                    value_3=f"{metrics['disk_write_mbps']:.1f}",
                                )
                            )

                        ui.timer(0.75, update_generation_display)
                    with ui.card().classes("studio-card w-full"):
                        ui.icon("lightbulb", color="primary", size="28px")
                        ui.label(t("Uma boa referência faz diferença.")).classes("font-semibold")
                        ui.label(
                            t(
                                "Use uma gravação limpa, com voz natural e volume confortável. Para personalizar o modelo, abra Treinar uma voz."
                            )
                        ).classes("studio-muted")
                        ui.label(
                            t(
                                "O modelo original é voltado a inglês e chinês; português depende do checkpoint e dos dados."
                            )
                        ).classes("text-xs studio-muted")

    @ui.page("/voices")
    def voices_page():
        paths = []
        with shell("Minhas vozes"):
            ui.label(t("Suas vozes, sempre à mão.")).classes("studio-title")
            ui.label(
                t("Uma voz salva guarda sua referência e transcrição. Isso não treina nem altera o modelo.")
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full"):
                name = ui.input(t("Nome da voz"), placeholder=t("Ex.: Minha narração")).props("outlined")
                upload_status = ui.label(t("Envie uma gravação curta."))
                audio_upload(paths, upload_status, reference=True)
                transcript = ui.textarea(t("Transcrição da referência (opcional)")).props("outlined rows=2")
                consent = ui.checkbox(t("Tenho autorização para usar esta gravação"))

                async def save():
                    if not consent.value:
                        ui.notify(t("Confirme a autorização para usar a gravação."), type="warning")
                        return
                    result = await action(service.save_voice, name.value, paths[-1] if paths else "", transcript.value)
                    if result:
                        ui.notify(t("Voz salva."), type="positive")
                        cards.refresh()

                ui.button(t("Salvar voz"), icon="add", on_click=save).props("no-caps")

            @ui.refreshable
            def cards():
                voices = service.read()["voices"]
                if not voices:
                    ui.label(t("Nenhuma voz salva ainda. Adicione a primeira acima.")).classes("studio-muted")
                for voice in voices:
                    with ui.card().classes("studio-card w-full"):
                        ui.label(voice["name"]).classes("text-lg font-semibold")
                        ui.label(voice["transcript"] or "Transcrição automática ao gerar.").classes("studio-muted")
                        ui.audio(voice["reference"]).classes("w-full")
                        ui.button(t("Criar áudio"), on_click=lambda: ui.navigate.to("/")).props("flat no-caps")

            cards()

    @ui.page("/history")
    def history_page():
        with shell("Histórico"):
            ui.label(t("Tudo o que você criou.")).classes("studio-title")
            entries = service.read()["history"]
            if not entries:
                with ui.card().classes("studio-card w-full"):
                    ui.icon("graphic_eq", size="40px", color="primary")
                    ui.label(t("Seu primeiro áudio vai aparecer aqui."))
                    ui.button(t("Criar áudio"), on_click=lambda: ui.navigate.to("/")).props("no-caps")
            for entry in entries:
                with ui.card().classes("studio-card w-full"):
                    ui.label(entry["text"]).classes("font-semibold")
                    ui.label(entry["created_at"][:19].replace("T", " ") + t(" UTC")).classes("text-xs studio-muted")
                    if entry.get("model_name") or entry["checkpoint"]:
                        ui.label(t("Modelo: ") + (entry.get("model_name") or Path(entry["checkpoint"]).name)).classes(
                            "text-sm studio-muted"
                        )
                    ui.audio(entry["path"]).classes("w-full")
                    ui.button(
                        t("Baixar WAV"), icon="download", on_click=lambda path=entry["path"]: ui.download.file(path)
                    ).props("flat no-caps")
                    if entry["rating"]:
                        ui.badge(t(entry["rating"]))
                        ui.label(entry["note"])

    @ui.page("/settings")
    def settings_page():
        with shell("Configurações"):
            ui.label(t("Controle o seu estúdio.")).classes("studio-title")
            hardware, settings = service.hardware(), service.read()["settings"]
            with ui.card().classes("studio-card w-full"):
                ui.label(t("Seu equipamento")).classes("font-semibold text-lg")
                ui.label(
                    hardware["name"]
                    + (
                        t(" · {value_0} GB de memória", value_0=hardware["memory"])
                        if hardware["cuda"]
                        else t(" · CUDA indisponível")
                    )
                )
                ui.label(
                    t(
                        "CUDA acelera o cálculo. TensorRT é uma otimização de execução e não está integrado a este estúdio."
                    )
                ).classes("studio-muted")
            with ui.expansion(t("Dicionário de pronúncia"), icon="spellcheck").classes("w-full"):
                ui.label(
                    t(
                        "Uma regra por linha. A substituição vale para o texto gerado, mantendo o roteiro original salvo."
                    )
                ).classes("studio-muted")
                pronunciation = ui.textarea(
                    t("Palavra = como ler"),
                    value="\n".join(
                        f"{key} = {value}" for key, value in service.read().get("pronunciations", {}).items()
                    ),
                ).props("outlined rows=5")

                async def save_pronunciations():
                    try:
                        await run.io_bound(service.set_pronunciations, pronunciation.value)
                        ui.notify(t("Dicionário salvo. Gere novamente os trechos afetados."), type="positive")
                    except Exception as error:
                        ui.notify(t(str(error)), type="negative")

                ui.button(t("Salvar pronúncias"), on_click=save_pronunciations).props("no-caps")
            with ui.card().classes("studio-card w-full"):
                ui.label(t("Downloads de transcrição e tradução")).classes("font-semibold text-lg")
                cache_only = ui.switch(
                    t("Usar somente modelos já baixados nessas duas ferramentas"),
                    value=service.read().get("offline", False),
                )
                ui.label(
                    t(
                        "Ative depois de baixar os modelos. Geração e treinamento também precisam de seus pesos no cache para uso sem internet."
                    )
                ).classes("studio-muted text-sm")
                ui.button(
                    t("Salvar preferência de cache"), on_click=lambda: action(service.set_offline, cache_only.value)
                ).props("flat no-caps")
            with ui.card().classes("studio-card w-full"):
                device = ui.select(
                    {"auto": t("Automático"), "cuda": t("NVIDIA / CUDA"), "cpu": t("CPU")},
                    value=settings["device"],
                    label=t("Onde gerar o áudio"),
                ).props("outlined")
                steps = ui.select(
                    {16: t("Rápido · 16 etapas"), 32: t("Equilibrado · 32 etapas"), 48: t("Mais etapas · 48")},
                    value=settings["steps"],
                    label=t("Etapas de geração"),
                ).props("outlined")
                ui.label(
                    t("Mais etapas usam mais tempo e podem mudar o resultado; compare os áudios para escolher.")
                ).classes("studio-muted")
                speed = ui.number(
                    t("Velocidade (1 = padrão)"), value=settings["speed"], min=0.3, max=2, step=0.1
                ).props("outlined")
                precision = ui.select(
                    {"fp16": "FP16", "fp32": "FP32"},
                    value=settings.get("precision", "fp16"),
                    label=t("Precisão na GPU"),
                ).props("outlined")

                async def save():
                    try:
                        await run.io_bound(service.settings, device.value, steps.value, speed.value, precision.value)
                        ui.notify(t("Configurações salvas."), type="positive")
                    except Exception as error:
                        ui.notify(t(str(error)), type="negative")

                ui.button(t("Salvar configurações"), icon="save", on_click=save).props("no-caps")
                ui.label(
                    t(
                        "Para melhorar a naturalidade, priorize referência limpa, textos corretos e ajuste fino revisado. Nenhuma opção garante qualidade superior."
                    )
                ).classes("studio-muted")


def main():
    parser = argparse.ArgumentParser(description="F5-TTS Studio — interface local em português")
    parser.add_argument("--port", type=int, default=7862)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--workspace", type=Path, default=None, help="Pasta para vozes, referências e histórico")
    args = parser.parse_args()
    service = StudioService(home=args.workspace)
    register_pages(service)
    ui.run(
        host="127.0.0.1",
        port=args.port,
        title="F5 Studio",
        reload=False,
        show=not args.no_browser,
        language=service.interface_language(),
        favicon="🎙️",
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
