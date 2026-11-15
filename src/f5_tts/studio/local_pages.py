"""User-facing local tools, without API keys or paid providers."""

from pathlib import Path

from nicegui import ui

from f5_tts.studio.i18n import localized_options


LANGUAGES = {"pt": "Português", "en": "Inglês", "es": "Espanhol", "fr": "Francês", "de": "Alemão"}


def register_local_pages(service, shell, action, audio_upload):
    t = service.translate_ui

    @ui.page("/projects")
    def narration_page():
        state = {"project": None, "selected": None, "mix": None}
        voices = {v["id"]: v["name"] for v in service.read()["voices"]}
        with shell("Projetos de narração"):
            ui.label(t("Histórias com mais de uma voz.")).classes("studio-title")
            ui.label(
                t(
                    "Escreva seu roteiro por parágrafos. Escolha quem fala em cada trecho, corrija apenas o que mudou e exporte a narração completa."
                )
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full"):
                project_choice = ui.select(
                    {p["id"]: p["name"] for p in service.narrations() if p.get("kind") != "story"},
                    label=t("Continuar um projeto"),
                ).props("outlined")

                async def open_project():
                    result = await action(service.narration, project_choice.value)
                    if result:
                        state.update(project=result["id"], selected=None, mix=result.get("mix"))
                        blocks.refresh()
                        output.set_source(result["mix"]["path"] if result.get("mix") else "")
                        downloads.refresh()
                        ui.notify(t("Projeto aberto."), type="positive")

                ui.button(t("Abrir projeto"), on_click=open_project).props("flat no-caps")
                with ui.expansion(t("Criar um projeto de narração"), value=not bool(service.narrations())).classes(
                    "w-full"
                ):
                    name = ui.input(t("Nome do projeto"), placeholder=t("Meu audiobook, aula ou diálogo")).props(
                        "outlined"
                    )
                    voice = ui.select(voices, label=t("Voz inicial para os trechos")).props("outlined")
                    ui.label(t("Salve referências em Minhas vozes antes de criar o projeto.")).classes(
                        "studio-muted text-sm"
                    )
                    script = ui.textarea(
                        t("Roteiro"),
                        placeholder=t(
                            "Separe os trechos por uma linha em branco. Depois você poderá trocar a voz de cada um."
                        ),
                    ).props("outlined rows=8")

                    async def create():
                        result = await action(service.create_narration, name.value, script.value, voice.value)
                        if result:
                            state.update(project=result["id"], selected=None, mix=None)
                            project_choice.set_options(
                                {p["id"]: p["name"] for p in service.narrations() if p.get("kind") != "story"}
                            )
                            project_choice.set_value(result["id"])
                            blocks.refresh()

                    ui.button(t("Criar roteiro por trechos"), icon="add", on_click=create).props("no-caps")
            with ui.expansion(t("Modelo para este roteiro (opcional)"), icon="school").classes("w-full"):
                trained = ui.select(service.projects(), label=t("Projeto treinado"), clearable=True).props("outlined")
                version = ui.select({}, label=t("Checkpoint")).props("outlined")

                async def list_models():
                    version.set_value(None)
                    models = await action(service.checkpoints, trained.value) if trained.value else []
                    if models is not None:
                        version.set_options({model: Path(model).name for model in models})

                trained.on_value_change(list_models)

                async def apply_model():
                    result = await action(service.set_narration_model, state["project"], trained.value, version.value)
                    if result:
                        state["mix"] = None
                        blocks.refresh()
                        downloads.refresh()
                        ui.notify(t("Modelo aplicado. Gere novamente os trechos pendentes."), type="positive")

                ui.button(t("Aplicar ao roteiro aberto"), on_click=apply_model).props("flat no-caps")
                ui.label(
                    t(
                        "Sem projeto treinado, é usado o modelo original. Alterar o modelo exige gerar os trechos novamente."
                    )
                ).classes("studio-muted text-sm")
            with ui.card().classes("studio-card w-full"):
                ui.label(t("Trechos do roteiro")).classes("text-lg font-semibold")

                @ui.refreshable
                def blocks():
                    if not state["project"]:
                        ui.label(t("Crie ou abra um projeto para começar."))
                        return
                    p = service.narration(state["project"])

                    def select(event):
                        if not event.selection:
                            return
                        block = next(b for b in p["blocks"] if b["id"] == event.selection[0]["id"])
                        state["selected"] = block["id"]
                        text.set_value(block["text"])
                        block_voice.set_value(block["voice"])
                        pause.set_value(block["pause"])
                        if block["render"]:
                            clip_player.set_source(block["render"]["path"])

                    ui.table(
                        columns=[
                            {"name": "number", "label": t("Ordem"), "field": "number"},
                            {"name": "text", "label": t("Trecho"), "field": "text", "align": "left"},
                            {"name": "voice", "label": t("Voz"), "field": "voice"},
                            {"name": "status", "label": t("Áudio"), "field": "status"},
                        ],
                        rows=[
                            {
                                "id": b["id"],
                                "number": i + 1,
                                "text": b["text"][:100],
                                "voice": voices.get(b["voice"], t("Voz removida")),
                                "status": t("Pronto") if service.block_ready(b) else t("Gerar/atualizar"),
                            }
                            for i, b in enumerate(p["blocks"])
                        ],
                        row_key="id",
                        selection="single",
                        on_select=select,
                        pagination=10,
                    ).classes("w-full")

                blocks()
                text = ui.textarea(t("Texto do trecho selecionado")).props("outlined rows=4")
                block_voice = ui.select(voices, label=t("Quem fala neste trecho?")).props("outlined")
                pause = ui.number(t("Pausa depois deste trecho (segundos)"), value=0.4, min=0, max=10, step=0.1).props(
                    "outlined"
                )
                clip_player = ui.audio("").classes("w-full")

                async def save():
                    result = await action(
                        service.edit_block,
                        state["project"],
                        state["selected"],
                        text.value,
                        block_voice.value,
                        pause.value,
                    )
                    if result:
                        state["mix"] = None
                        blocks.refresh()
                        downloads.refresh()
                        ui.notify(t("Trecho salvo."), type="positive")

                async def render_one():
                    result = await action(service.render_block, state["project"], state["selected"])
                    if result:
                        clip_player.set_source(result["path"])
                        state["mix"] = None
                        blocks.refresh()
                        downloads.refresh()

                async def move(direction):
                    result = await action(service.move_block, state["project"], state["selected"], direction)
                    if result:
                        state["mix"] = None
                        blocks.refresh()
                        downloads.refresh()

                with ui.row():
                    ui.button(t("Salvar trecho"), icon="save", on_click=save).props("no-caps")
                    ui.button(t("Gerar só este trecho"), icon="play_arrow", on_click=render_one).props(
                        "outline no-caps"
                    )
                    ui.button(icon="arrow_upward", on_click=lambda: move(-1)).props("flat").tooltip(
                        t("Mover para cima")
                    )
                    ui.button(icon="arrow_downward", on_click=lambda: move(1)).props("flat").tooltip(
                        t("Mover para baixo")
                    )
                ui.label(
                    t("Salve as alterações antes de gerar. Alterar o texto ou a voz exige gerar esse trecho novamente.")
                ).classes("studio-muted text-sm")
            with ui.card().classes("studio-card w-full"):
                ui.label(t("Narração completa")).classes("text-lg font-semibold")
                progress = ui.label("")
                ui.timer(1, lambda: progress.set_text(t(service.render_status)))
                output = ui.audio("").classes("w-full")

                async def render_all():
                    render.disable()
                    try:
                        result = await action(service.render_narration, state["project"])
                        if result:
                            state["mix"] = result
                            output.set_source(result["path"])
                            blocks.refresh()
                            downloads.refresh()
                    finally:
                        render.enable()

                render = ui.button(
                    t("Gerar trechos pendentes e juntar"), icon="auto_awesome", on_click=render_all
                ).props("no-caps")

                ui.button(
                    t("Parar depois do trecho atual"), icon="stop", on_click=lambda: action(service.stop_render)
                ).props("flat no-caps")

                @ui.refreshable
                def downloads():
                    if state["mix"]:
                        ui.button(
                            t("Baixar narração WAV"),
                            icon="download",
                            on_click=lambda: ui.download.file(state["mix"]["path"]),
                        ).props("flat no-caps")
                        ui.button(
                            t("Baixar legendas SRT"),
                            icon="subtitles",
                            on_click=lambda: ui.download.file(state["mix"]["srt"]),
                        ).props("flat no-caps")

                downloads()
                ui.label(
                    t(
                        "As legendas acompanham os trechos gerados, não cada palavra. As pausas são aplicadas entre os trechos."
                    )
                ).classes("studio-muted text-sm")

    @ui.page("/transcription")
    def transcription_page():
        paths, state = [], {"record": None, "segment": None}
        with shell("Transcrição e legendas"):
            ui.label(t("Do áudio para o texto.")).classes("studio-title")
            ui.label(
                t("Transcreva, revise e baixe TXT ou SRT. O processamento acontece no seu PC, sem API paga.")
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full"):
                previous = ui.select(
                    {t["id"]: t["name"] for t in service.transcripts()}, label=t("Abrir transcrição salva")
                ).props("outlined")

                async def open_saved():
                    result = await action(service.transcript, previous.value)
                    if result:
                        state.update(record=result, segment=None)
                        result_area.refresh()

                ui.button(t("Abrir"), on_click=open_saved).props("flat no-caps")
                status = ui.label(t("Envie um arquivo de áudio."))
                audio_upload(paths, status)
                language = ui.select(
                    {**localized_options(LANGUAGES, t), "": t("Detectar automaticamente")},
                    value="pt",
                    label=t("Idioma do áudio"),
                ).props("outlined")
                model = ui.select(
                    {
                        "openai/whisper-small": t("Whisper Small · mais leve"),
                        "openai/whisper-large-v3-turbo": t("Whisper Turbo · mais pesado"),
                    },
                    value="openai/whisper-small",
                    label=t("Modelo local"),
                ).props("outlined")
                ui.label(
                    t(
                        "Na primeira vez, será necessário baixar os pesos. Nenhum áudio é enviado ao fornecedor do modelo."
                    )
                ).classes("studio-muted text-sm")

                async def transcribe():
                    transcribe_button.disable()
                    status.set_text(t("Transcrevendo localmente… Aguarde o download inicial, se necessário."))
                    try:
                        result = await action(
                            service.transcribe_audio, paths[-1] if paths else "", language.value, model.value
                        )
                        if result:
                            state.update(record=result, segment=None)
                            status.set_text(t("Texto reconhecido. Ouça e revise os trechos abaixo."))
                            previous.set_options({t["id"]: t["name"] for t in service.transcripts()})
                            result_area.refresh()
                        else:
                            status.set_text(t("Transcrição não concluída. Confira a mensagem."))
                    finally:
                        transcribe_button.enable()

                transcribe_button = ui.button(
                    t("Transcrever e criar legendas"), icon="subtitles", on_click=transcribe
                ).props("no-caps")

            @ui.refreshable
            def result_area():
                if not state["record"]:
                    return
                record = service.transcript(state["record"]["id"])
                state["record"] = record
                with ui.card().classes("studio-card w-full"):
                    ui.audio(record["source"]).classes("w-full")

                    def select(event):
                        if event.selection:
                            index = event.selection[0]["id"]
                            state["segment"] = index
                            correction.set_value(record["segments"][index]["text"])

                    ui.table(
                        columns=[
                            {"name": "start", "label": t("Início (s)"), "field": "start"},
                            {"name": "end", "label": t("Fim (s)"), "field": "end"},
                            {"name": "text", "label": t("Texto"), "field": "text", "align": "left"},
                        ],
                        rows=[{"id": i, **segment} for i, segment in enumerate(record["segments"])],
                        selection="single",
                        on_select=select,
                        pagination=10,
                    ).classes("w-full")
                    correction = ui.textarea(t("Corrigir o trecho selecionado")).props("outlined rows=3")

                    async def save():
                        if state["segment"] is None:
                            ui.notify(t("Selecione um trecho na tabela."), type="warning")
                            return
                        updated = await action(
                            service.edit_transcript, record["id"], state["segment"], correction.value
                        )
                        if updated:
                            state["record"] = updated
                            result_area.refresh()

                    ui.button(t("Salvar correção e atualizar legendas"), icon="save", on_click=save).props("no-caps")
                    with ui.row():
                        ui.button(t("Baixar TXT"), on_click=lambda: ui.download.file(record["txt"])).props(
                            "flat no-caps"
                        )
                        ui.button(t("Baixar SRT"), on_click=lambda: ui.download.file(record["srt"])).props(
                            "flat no-caps"
                        )
                with ui.expansion(t("Traduzir as legendas no meu PC"), icon="translate").classes("w-full"):
                    ui.label(
                        t(
                            "A tradução usa o modelo local NLLB-200 (download inicial). Revise o resultado. O modelo tem licença CC-BY-NC-4.0."
                        )
                    ).classes("studio-muted")
                    source = ui.select(
                        localized_options(LANGUAGES, t),
                        value=record["language"] if record["language"] in LANGUAGES else "pt",
                        label=t("Idioma de origem"),
                    ).props("outlined")
                    target = ui.select(
                        localized_options(LANGUAGES, t),
                        value="en" if source.value != "en" else "pt",
                        label=t("Traduzir para"),
                    ).props("outlined")

                    async def translate():
                        translated = await action(
                            service.translate_transcript, record["id"], source.value, target.value
                        )
                        if translated:
                            state["record"] = translated
                            previous.set_options({t["id"]: t["name"] for t in service.transcripts()})
                            result_area.refresh()

                    ui.button(t("Criar versão traduzida"), icon="translate", on_click=translate).props("no-caps")

            result_area()

    @ui.page("/revoice")
    def revoice_page():
        state = {"output": None}
        with shell("Regravar com outra voz"):
            ui.label(t("Outra voz para o seu texto.")).classes("studio-title")
            ui.label(
                t(
                    "Use uma transcrição revisada ou traduzida e gere uma nova narração. Isso é regravação por texto: não preserva exatamente a emoção, o ritmo ou a sincronização do original."
                )
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full"):
                transcript = ui.select(
                    {t["id"]: t["name"] for t in service.transcripts()}, label=t("Transcrição salva")
                ).props("outlined")
                text = ui.textarea(t("Texto revisado para regravar")).props("outlined rows=8")

                async def load():
                    result = await action(service.transcript, transcript.value)
                    if result:
                        text.set_value(result["text"])

                ui.button(t("Carregar texto"), on_click=load).props("flat no-caps")
                voice = ui.select(
                    {v["id"]: v["name"] for v in service.read()["voices"]}, label=t("Voz de destino")
                ).props("outlined")
                saved_model = ui.select(
                    service.model_choices(),
                    value="original",
                    label=t("Modelo para regravar"),
                ).props("outlined")
                ui.link(t("Cadastrar outro modelo"), "/models").classes("text-sm")
                trained_model = ui.select(
                    service.projects(), label=t("Usar um projeto treinado (opcional)"), clearable=True
                ).props("outlined")
                trained_version = ui.select({}, label=t("Checkpoint do projeto treinado")).props("outlined")

                async def choose_version():
                    if trained_model.value:
                        saved_model.set_value("original")
                    trained_version.set_value(None)
                    versions = await action(service.checkpoints, trained_model.value) if trained_model.value else []
                    if versions is not None:
                        trained_version.set_options({version: Path(version).name for version in versions})

                trained_model.on_value_change(choose_version)
                saved_model.on_value_change(
                    lambda: trained_model.set_value(None) if saved_model.value != "original" else None
                )
                ui.label(
                    t(
                        "A qualidade e o idioma da voz dependem do checkpoint F5-TTS. Os modelos originais são voltados a inglês e chinês."
                    )
                ).classes("studio-muted text-sm")

                async def generate():
                    if not voice.value:
                        ui.notify(t("Escolha uma voz salva."), type="warning")
                        return
                    result = await action(
                        service.generate,
                        text.value,
                        None,
                        "",
                        voice.value,
                        trained_model.value,
                        trained_version.value,
                        saved_model.value,
                    )
                    if result:
                        state["output"] = result
                        player.set_source(result["path"])
                        download.enable()

                ui.button(t("Gerar nova narração"), icon="record_voice_over", on_click=generate).props("no-caps")
                player = ui.audio("").classes("w-full")
                download = ui.button(
                    t("Baixar WAV"), icon="download", on_click=lambda: ui.download.file(state["output"]["path"])
                ).props("flat no-caps")
                download.disable()

    @ui.page("/audio-tools")
    def audio_tools_page():
        paths, state = [], {"output": None}
        with shell("Ferramentas de áudio"):
            ui.label(t("Prepare e exporte seu áudio.")).classes("studio-title")
            ui.label(
                t(
                    "Ferramentas locais com FFmpeg. A redução de ruído é simples: não equivale a separar voz, música ou falantes com IA."
                )
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full"):
                status = ui.label(t("Envie o áudio que deseja processar."))
                audio_upload(paths, status)
                effect = ui.select(
                    {
                        "none": t("Só converter o formato"),
                        "normalize": t("Equilibrar volume"),
                        "noise": t("Reduzir ruído contínuo"),
                        "silence": t("Cortar silêncios"),
                    },
                    value="normalize",
                    label=t("O que fazer?"),
                ).props("outlined")
                fmt = ui.select({"wav": t("WAV"), "mp3": t("MP3")}, value="wav", label=t("Formato de saída")).props(
                    "outlined"
                )

                async def process():
                    result = await action(service.process_audio, paths[-1] if paths else "", effect.value, fmt.value)
                    if result:
                        state["output"] = result
                        player.set_source(result)
                        download.enable()
                        status.set_text(t("Arquivo pronto. Ouça antes de usar."))

                ui.button(t("Processar no meu PC"), icon="tune", on_click=process).props("no-caps")
                player = ui.audio("").classes("w-full")
                download = ui.button(
                    t("Baixar arquivo"), icon="download", on_click=lambda: ui.download.file(state["output"])
                ).props("flat no-caps")
                download.disable()
