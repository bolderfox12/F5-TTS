"""NiceGUI desktop-browser studio. Run with f5-tts_studio."""

import argparse
from pathlib import Path

from nicegui import app, run, ui

from f5_tts.studio.i18n import INTERFACE_LANGUAGES
from f5_tts.studio.local_pages import register_local_pages
from f5_tts.studio.microphone import Microphone
from f5_tts.studio.model_pages import register_model_pages
from f5_tts.studio.service import StudioService
from f5_tts.studio.story_pages import register_story_pages


CSS = """
body { background: #f3f6f8; color: #172c37; font-family: Inter, system-ui, sans-serif; }
.q-drawer { background: #102e38; color: #d2e5e8; }
.studio-card { background: white; border: 1px solid #e0e8ec; border-radius: 18px; box-shadow: none; padding: 24px; }
.studio-hero { background: linear-gradient(110deg,#113540,#176068); color: white; border-radius: 20px; padding: 32px; }
.studio-title { font-size: 30px; font-weight: 700; letter-spacing: -1px; }
.studio-muted { color: #627781; }
.studio-nav { width: 100%; justify-content: flex-start; padding: 12px 16px; border-radius: 10px; }
.q-stepper { box-shadow: none; border: 1px solid #e0e8ec; border-radius: 18px; width: 100%; }
.q-uploader { box-shadow: none; border: 1px dashed #75a9ad; width: 100%; }
.q-field { width: 100%; }
@media(max-width:700px) { .studio-card,.studio-hero { padding: 18px; } .studio-title {font-size:25px;} }
"""


def register_pages(service):
    t = service.translate_ui
    app.on_shutdown(service.stop_training)

    async def action(fn, *args):
        try:
            return await run.io_bound(fn, *args)
        except Exception as error:
            ui.notify(t(str(error)), type="negative", close_button=True, timeout=10000)
            return None

    def shell(section):
        ui.context.client.page.language = service.interface_language()
        ui.colors(primary="#137d80", secondary="#294d5b", accent="#2bb5a7")
        ui.add_css(CSS)
        with ui.left_drawer(value=True).props("show-if-above bordered").classes("p-5") as drawer:
            ui.label(t("F5 / STUDIO")).classes("text-xl font-bold mt-4")
            ui.label(t("Seu espaço de voz")).classes("text-sm opacity-70 mb-8")
            for title, icon, route in [
                ("Criar áudio", "graphic_eq", "/"),
                ("Minhas vozes", "record_voice_over", "/voices"),
                ("Meus modelos", "inventory_2", "/models"),
                ("Projetos de narração", "article", "/projects"),
                ("Histórias e personagens", "auto_stories", "/stories"),
                ("Transcrição e legendas", "subtitles", "/transcription"),
                ("Regravar áudio", "mic", "/revoice"),
                ("Ferramentas de áudio", "equalizer", "/audio-tools"),
                ("Treinar uma voz", "school", "/training"),
                ("Histórico", "history", "/history"),
                ("Configurações", "tune", "/settings"),
            ]:
                ui.button(t(title), icon=icon, on_click=lambda target=route: ui.navigate.to(target)).props(
                    "flat no-caps"
                ).classes("studio-nav")
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

    async def receive(event, paths, label):
        try:
            path = service.upload_path(event.file.name)
            await event.file.save(path)
            paths.append(str(path))
            label.set_text(t("{value_0} arquivo(s) enviado(s).", value_0=len(paths)))
        except Exception as error:
            ui.notify(t(str(error)), type="negative")

    def audio_upload(paths, label, multiple=False, reference=False, on_reference=None):
        if reference:

            async def receive_recording(event):
                result = await action(service.microphone_reference, event.args)
                if result:
                    paths.append(result)
                    if on_reference:
                        on_reference()
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
            on_upload=lambda event: receive(event, paths, label),
            on_rejected=lambda: ui.notify(t("Arquivo recusado. O limite é 100 MB por arquivo."), type="warning"),
        ).props('accept=".wav,.mp3,.flac,.ogg,.m4a,.aac"').classes("w-full")

    register_local_pages(service, shell, action, audio_upload)
    register_model_pages(service, shell, action)
    register_story_pages(service, shell, action)

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
                    voices = {v["id"]: v["name"] for v in service.read()["voices"]}
                    voice = ui.select(voices, label=t("Voz salva (opcional)"), clearable=True)
                    ui.label(
                        t("Grave pelo microfone ou envie uma referência curta. Prefira até 12 segundos, sem música.")
                    ).classes("studio-muted")
                    upload_status = ui.label(t("Nenhum arquivo enviado.")).classes("text-sm")
                    audio_upload(paths, upload_status, reference=True, on_reference=lambda: voice.set_value(None))
                    reference_text = ui.textarea(
                        label=t("O que é dito na referência? (opcional)"),
                        placeholder=t("Vazio: transcrição automática."),
                    ).props("outlined rows=2")
                    saved_model = ui.select(
                        service.model_choices(),
                        value="original",
                        label=t("Modelo para gerar áudio"),
                    ).props("outlined")
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
                    status = ui.label(t("Pronto para começar.")).classes("studio-muted")

                    async def generate():
                        button.disable()
                        status.set_text(t("Gerando… No primeiro uso, os modelos serão baixados. Aguarde."))
                        try:
                            result = await action(
                                service.generate,
                                text.value,
                                paths[-1] if paths else None,
                                reference_text.value,
                                voice.value,
                                trained_project.value,
                                trained_checkpoint.value,
                                saved_model.value,
                            )
                            if result:
                                generated.update(result)
                                player.set_source(result["path"])
                                download.enable()
                                status.set_text(t("Áudio pronto. Você também pode encontrá-lo no Histórico."))
                            else:
                                status.set_text(t("Geração não concluída. Confira a mensagem e tente novamente."))
                        finally:
                            button.enable()

                    button = (
                        ui.button(t("Gerar áudio"), icon="play_arrow", on_click=generate)
                        .props("no-caps unelevated")
                        .classes("w-full py-3")
                    )
                with ui.column().classes("w-full md:w-1/3 gap-5"):
                    with ui.card().classes("studio-card w-full"):
                        ui.label(t("03 / Resultado")).classes("font-semibold text-lg")
                        player = ui.audio("").classes("w-full")
                        download = ui.button(
                            t("Baixar WAV"), icon="download", on_click=lambda: ui.download.file(generated["path"])
                        ).props("flat no-caps")
                        download.disable()
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

    @ui.page("/training")
    def training_page():
        uploaded = []
        state = {"project": "", "selected": "", "test": None}
        with shell("Treinar uma voz"):
            ui.label(t("Sua voz. Um passo de cada vez.")).classes("studio-title")
            ui.label(
                t(
                    "Envie áudios prontos, revise os textos e ajuste uma cópia do modelo. Nada é treinado automaticamente."
                )
            ).classes("studio-muted")
            with ui.stepper().props("vertical color=primary").classes("w-full") as stepper:
                with ui.step("project", title=t("1. Criar ou abrir um projeto")):
                    name = ui.input(t("Nome do projeto"), value=t("minha_voz")).props("outlined")
                    available = service.projects()
                    if available:
                        ui.select(
                            available,
                            label=t("Ou continue um projeto salvo"),
                            on_change=lambda e: name.set_value(e.value),
                        ).props("outlined")
                    project_status = ui.label(t("Use um nome sem espaços ou acentos."))

                    async def open_project():
                        project = await action(service.project, name.value)
                        if project:
                            state["project"] = project.name
                            project_status.set_text(t("Projeto aberto: ") + project.name)
                            rows.refresh()
                            stepper.next()

                    ui.button(t("Abrir e continuar"), icon="arrow_forward", on_click=open_project).props("no-caps")
                with ui.step("upload", title=t("2. Enviar seus áudios prontos")):
                    ui.markdown(
                        t(
                            "Selecione **um ou vários arquivos** da mesma pessoa. O aplicativo gera os textos e divide gravações longas. Depois você confere tudo. Não é preciso criar planilhas."
                        )
                    )
                    ui.label(
                        t("Prefira gravações sem música ou ruído. O primeiro uso baixa o modelo de transcrição.")
                    ).classes("studio-muted")
                    upload_status = ui.label(t("Nenhum arquivo enviado."))
                    audio_upload(uploaded, upload_status, multiple=True)
                    language = ui.select(
                        {"pt": t("Português"), "en": t("Inglês"), "es": t("Espanhol"), "": t("Detectar idioma")},
                        value="pt",
                        label=t("Idioma das gravações"),
                    ).props("outlined")
                    consent = ui.checkbox(t("Tenho autorização para usar estas gravações no treinamento"))
                    import_status = ui.label("").classes("whitespace-pre-wrap")

                    async def import_audio():
                        import_button.disable()
                        import_status.set_text(
                            t("Importando e transcrevendo… O processamento pode levar alguns minutos.")
                        )
                        try:
                            result = await action(
                                service.import_audio, state["project"], list(uploaded), language.value, consent.value
                            )
                            if result is not None:
                                import_status.set_text(t(str(result)))
                                uploaded.clear()
                                rows.refresh()
                        finally:
                            import_button.enable()

                    import_button = ui.button(
                        t("Importar e gerar os textos"), icon="upload", on_click=import_audio
                    ).props("no-caps")
                    ui.button(t("Revisar exemplos"), icon="arrow_forward", on_click=stepper.next).props("flat no-caps")
                with ui.step("review", title=t("3. Ouvir, corrigir e aprovar")):
                    ui.label(
                        t(
                            "Selecione uma linha, ouça e confira se o texto corresponde à fala. Somente exemplos aprovados entram no treino."
                        )
                    ).classes("studio-muted")
                    sample_audio = ui.audio("").classes("w-full")
                    correction = ui.textarea(t("Texto correto")).props("outlined rows=3")
                    approved = ui.checkbox(t("Ouvi este trecho e aprovo seu uso"))

                    def select_sample(event):
                        selected = event.selection
                        if not selected:
                            state["selected"] = ""
                            return
                        row = selected[0]
                        state["selected"] = row["id"]
                        correction.set_value(row["text"])
                        approved.set_value(row["approved"])
                        p = service.project(state["project"])
                        sample_audio.set_source(str(p.path / "wavs" / (row["id"] + ".wav")))

                    @ui.refreshable
                    def rows():
                        if not state["project"]:
                            ui.label(t("Abra um projeto para ver os exemplos."))
                            return
                        p = service.project(state["project"])
                        examples = p.read()["examples"]
                        approved_examples = [example for example in examples if example["approved"]]
                        ui.label(
                            t(
                                "{value_0} exemplos · {value_1} aprovados · {value_2} segundos aprovados",
                                value_0=len(examples),
                                value_1=len(approved_examples),
                                value_2=round(sum(example["duration"] for example in approved_examples), 1),
                            )
                        )
                        items = [
                            {"id": row[0], "approved": row[1], "text": row[2], "seconds": row[3]} for row in p.rows()
                        ]
                        ui.table(
                            columns=[
                                {"name": "text", "label": t("Texto reconhecido"), "field": "text", "align": "left"},
                                {"name": "approved", "label": t("Aprovado"), "field": "approved"},
                                {"name": "seconds", "label": t("Segundos"), "field": "seconds"},
                            ],
                            rows=items,
                            row_key="id",
                            selection="single",
                            on_select=select_sample,
                            pagination=8,
                        ).classes("w-full")

                    rows()

                    async def save_review():
                        try:
                            await run.io_bound(
                                service.review, state["project"], state["selected"], correction.value, approved.value
                            )
                            ui.notify(t("Revisão salva."), type="positive")
                            rows.refresh()
                        except Exception as error:
                            ui.notify(t(str(error)), type="negative")

                    ui.button(t("Salvar revisão deste trecho"), icon="check", on_click=save_review).props("no-caps")
                    prepare_status = ui.label("").classes("whitespace-pre-wrap")

                    async def prepare():
                        result = await action(service.prepare, state["project"])
                        if result:
                            prepare_status.set_text(t("Exemplos preparados."))
                            stepper.next()

                    ui.label(
                        t(
                            "Mínimo para testar o fluxo: 3 trechos e 15 segundos aprovados. Isso não é suficiente para garantir uma boa voz."
                        )
                    ).classes("text-sm studio-muted")
                    ui.button(t("Preparar e continuar"), icon="arrow_forward", on_click=prepare).props("no-caps")
                with ui.step("train", title=t("4. Treinar e acompanhar")):
                    ui.markdown(
                        t(
                            "Requer **GPU NVIDIA/CUDA**. O modelo original é preservado. Novos treinos do mesmo projeto retomam seus checkpoints; crie outro projeto para um experimento independente."
                        )
                    )
                    epochs = ui.select(
                        {
                            5: t("Teste rápido · 5 passagens"),
                            20: t("Ajuste inicial · 20 passagens"),
                            50: t("Ajuste prolongado · 50 passagens"),
                        },
                        value=5,
                        label=t("Perfil de treinamento"),
                    ).props("outlined")
                    ui.label(t("Mais passagens não garantem melhoria. Comece pelo teste rápido.")).classes(
                        "studio-muted"
                    )

                    async def start():
                        try:
                            await run.io_bound(service.start_training, state["project"], epochs.value)
                            ui.notify(t("Treinamento iniciado."), type="positive")
                        except Exception as error:
                            ui.notify(t(str(error)), type="negative")

                    with ui.row():
                        ui.button(t("Iniciar treinamento"), icon="play_arrow", on_click=start).props("no-caps")
                        ui.button(t("Parar"), icon="stop", on_click=lambda: action(service.stop_training)).props(
                            "outline no-caps"
                        )
                    job = ui.label(t(service.job["message"])).classes(
                        "whitespace-pre-wrap w-full bg-slate-100 p-4 rounded-lg text-sm"
                    )
                    ui.timer(1, lambda: job.set_text(t(service.job["message"])))
                    ui.button(t("Testar uma versão"), icon="arrow_forward", on_click=stepper.next).props("flat no-caps")
                with ui.step("test", title=t("5. Ouvir o resultado e avaliar")):
                    checkpoint = ui.select({}, label=t("Versão treinada (checkpoint)")).props("outlined")

                    async def refresh_checkpoints():
                        result = await action(service.checkpoints, state["project"])
                        if result is not None:
                            checkpoint.set_options({p: Path(p).name for p in result})
                            if not result:
                                ui.notify(
                                    t("Nenhum checkpoint ainda. Aguarde o treino salvar uma versão."), type="info"
                                )

                    ui.button(t("Buscar versões"), icon="refresh", on_click=refresh_checkpoints).props("flat no-caps")
                    ref_paths = []
                    ref_status = ui.label(t("Envie uma referência curta para comparar as versões."))
                    audio_upload(ref_paths, ref_status, reference=True)
                    ref_text = ui.textarea(t("Texto da referência (opcional)")).props("outlined rows=2")
                    test_text = ui.textarea(
                        t("Frase nova para testar"), value=t("Olá! Este é um teste da minha voz.")
                    ).props("outlined rows=2")
                    result_audio = ui.audio("").classes("w-full")

                    async def test_voice():
                        result = await action(
                            service.generate,
                            test_text.value,
                            ref_paths[-1] if ref_paths else None,
                            ref_text.value,
                            None,
                            state["project"],
                            checkpoint.value,
                        )
                        if result:
                            state["test"] = result
                            result_audio.set_source(result["path"])

                    ui.button(t("Gerar teste desta versão"), icon="play_arrow", on_click=test_voice).props("no-caps")
                    rating = ui.radio({"Ficou bom": t("Ficou bom"), "Precisa melhorar": t("Precisa melhorar")})
                    note = ui.textarea(t("O que precisa melhorar?")).props("outlined rows=2")

                    async def rate():
                        if not state["test"]:
                            ui.notify(t("Gere um teste primeiro."), type="warning")
                            return
                        try:
                            await run.io_bound(service.rate, state["test"]["id"], rating.value, note.value)
                            ui.notify(t("Avaliação salva no histórico."), type="positive")
                        except Exception as error:
                            ui.notify(t(str(error)), type="negative")

                    ui.button(t("Guardar avaliação"), icon="save", on_click=rate).props("no-caps")
                    ui.label(
                        t(
                            "Avaliações não alteram os pesos. Para ensinar uma correção, envie um áudio correto, revise e treine novamente."
                        )
                    ).classes("studio-muted")

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

                async def save():
                    try:
                        await run.io_bound(service.settings, device.value, steps.value, speed.value)
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
