"""User-facing local tools, without API keys or paid providers."""

from pathlib import Path

from nicegui import ui


LANGUAGES = {"pt": "Português", "en": "Inglês", "es": "Espanhol", "fr": "Francês", "de": "Alemão"}


def register_local_pages(service, shell, action, audio_upload):
    @ui.page("/projects")
    def narration_page():
        state = {"project": None, "selected": None, "mix": None}
        voices = {v["id"]: v["name"] for v in service.read()["voices"]}
        with shell("Projetos de narração"):
            ui.label("Histórias com mais de uma voz.").classes("studio-title")
            ui.label(
                "Escreva seu roteiro por parágrafos. Escolha quem fala em cada trecho, corrija apenas o que mudou e exporte a narração completa."
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full"):
                project_choice = ui.select(
                    {p["id"]: p["name"] for p in service.narrations()}, label="Continuar um projeto"
                ).props("outlined")

                async def open_project():
                    result = await action(service.narration, project_choice.value)
                    if result:
                        state.update(project=result["id"], selected=None, mix=result.get("mix"))
                        blocks.refresh()
                        output.set_source(result["mix"]["path"] if result.get("mix") else "")
                        downloads.refresh()
                        ui.notify("Projeto aberto.", type="positive")

                ui.button("Abrir projeto", on_click=open_project).props("flat no-caps")
                with ui.expansion("Criar um projeto de narração", value=not bool(service.narrations())).classes(
                    "w-full"
                ):
                    name = ui.input("Nome do projeto", placeholder="Meu audiobook, aula ou diálogo").props("outlined")
                    voice = ui.select(voices, label="Voz inicial para os trechos").props("outlined")
                    ui.label("Salve referências em Minhas vozes antes de criar o projeto.").classes(
                        "studio-muted text-sm"
                    )
                    script = ui.textarea(
                        "Roteiro",
                        placeholder="Separe os trechos por uma linha em branco. Depois você poderá trocar a voz de cada um.",
                    ).props("outlined rows=8")

                    async def create():
                        result = await action(service.create_narration, name.value, script.value, voice.value)
                        if result:
                            state.update(project=result["id"], selected=None, mix=None)
                            project_choice.set_options({p["id"]: p["name"] for p in service.narrations()})
                            project_choice.set_value(result["id"])
                            blocks.refresh()

                    ui.button("Criar roteiro por trechos", icon="add", on_click=create).props("no-caps")
            with ui.expansion("Modelo para este roteiro (opcional)", icon="school").classes("w-full"):
                trained = ui.select(service.projects(), label="Projeto treinado", clearable=True).props("outlined")
                version = ui.select({}, label="Checkpoint").props("outlined")

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
                        ui.notify("Modelo aplicado. Gere novamente os trechos pendentes.", type="positive")

                ui.button("Aplicar ao roteiro aberto", on_click=apply_model).props("flat no-caps")
                ui.label(
                    "Sem projeto treinado, é usado o modelo original. Alterar o modelo exige gerar os trechos novamente."
                ).classes("studio-muted text-sm")
            with ui.card().classes("studio-card w-full"):
                ui.label("Trechos do roteiro").classes("text-lg font-semibold")

                @ui.refreshable
                def blocks():
                    if not state["project"]:
                        ui.label("Crie ou abra um projeto para começar.")
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
                            {"name": "number", "label": "Ordem", "field": "number"},
                            {"name": "text", "label": "Trecho", "field": "text", "align": "left"},
                            {"name": "voice", "label": "Voz", "field": "voice"},
                            {"name": "status", "label": "Áudio", "field": "status"},
                        ],
                        rows=[
                            {
                                "id": b["id"],
                                "number": i + 1,
                                "text": b["text"][:100],
                                "voice": voices.get(b["voice"], "Voz removida"),
                                "status": "Pronto" if service.block_ready(b) else "Gerar/atualizar",
                            }
                            for i, b in enumerate(p["blocks"])
                        ],
                        row_key="id",
                        selection="single",
                        on_select=select,
                        pagination=10,
                    ).classes("w-full")

                blocks()
                text = ui.textarea("Texto do trecho selecionado").props("outlined rows=4")
                block_voice = ui.select(voices, label="Quem fala neste trecho?").props("outlined")
                pause = ui.number("Pausa depois deste trecho (segundos)", value=0.4, min=0, max=10, step=0.1).props(
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
                        ui.notify("Trecho salvo.", type="positive")

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
                    ui.button("Salvar trecho", icon="save", on_click=save).props("no-caps")
                    ui.button("Gerar só este trecho", icon="play_arrow", on_click=render_one).props("outline no-caps")
                    ui.button(icon="arrow_upward", on_click=lambda: move(-1)).props("flat").tooltip("Mover para cima")
                    ui.button(icon="arrow_downward", on_click=lambda: move(1)).props("flat").tooltip("Mover para baixo")
                ui.label(
                    "Salve as alterações antes de gerar. Alterar o texto ou a voz exige gerar esse trecho novamente."
                ).classes("studio-muted text-sm")
            with ui.card().classes("studio-card w-full"):
                ui.label("Narração completa").classes("text-lg font-semibold")
                progress = ui.label("")
                ui.timer(1, lambda: progress.set_text(service.render_status))
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

                render = ui.button("Gerar trechos pendentes e juntar", icon="auto_awesome", on_click=render_all).props(
                    "no-caps"
                )

                ui.button(
                    "Parar depois do trecho atual", icon="stop", on_click=lambda: action(service.stop_render)
                ).props("flat no-caps")

                @ui.refreshable
                def downloads():
                    if state["mix"]:
                        ui.button(
                            "Baixar narração WAV",
                            icon="download",
                            on_click=lambda: ui.download.file(state["mix"]["path"]),
                        ).props("flat no-caps")
                        ui.button(
                            "Baixar legendas SRT",
                            icon="subtitles",
                            on_click=lambda: ui.download.file(state["mix"]["srt"]),
                        ).props("flat no-caps")

                downloads()
                ui.label(
                    "As legendas acompanham os trechos gerados, não cada palavra. As pausas são aplicadas entre os trechos."
                ).classes("studio-muted text-sm")

    @ui.page("/transcription")
    def transcription_page():
        paths, state = [], {"record": None, "segment": None}
        with shell("Transcrição e legendas"):
            ui.label("Do áudio para o texto.").classes("studio-title")
            ui.label(
                "Transcreva, revise e baixe TXT ou SRT. O processamento acontece no seu PC, sem API paga."
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full"):
                previous = ui.select(
                    {t["id"]: t["name"] for t in service.transcripts()}, label="Abrir transcrição salva"
                ).props("outlined")

                async def open_saved():
                    result = await action(service.transcript, previous.value)
                    if result:
                        state.update(record=result, segment=None)
                        result_area.refresh()

                ui.button("Abrir", on_click=open_saved).props("flat no-caps")
                status = ui.label("Envie um arquivo de áudio.")
                audio_upload(paths, status)
                language = ui.select(
                    {**LANGUAGES, "": "Detectar automaticamente"}, value="pt", label="Idioma do áudio"
                ).props("outlined")
                model = ui.select(
                    {
                        "openai/whisper-small": "Whisper Small · mais leve",
                        "openai/whisper-large-v3-turbo": "Whisper Turbo · mais pesado",
                    },
                    value="openai/whisper-small",
                    label="Modelo local",
                ).props("outlined")
                ui.label(
                    "Na primeira vez, será necessário baixar os pesos. Nenhum áudio é enviado ao fornecedor do modelo."
                ).classes("studio-muted text-sm")

                async def transcribe():
                    transcribe_button.disable()
                    status.set_text("Transcrevendo localmente… Aguarde o download inicial, se necessário.")
                    try:
                        result = await action(
                            service.transcribe_audio, paths[-1] if paths else "", language.value, model.value
                        )
                        if result:
                            state.update(record=result, segment=None)
                            status.set_text("Texto reconhecido. Ouça e revise os trechos abaixo.")
                            previous.set_options({t["id"]: t["name"] for t in service.transcripts()})
                            result_area.refresh()
                        else:
                            status.set_text("Transcrição não concluída. Confira a mensagem.")
                    finally:
                        transcribe_button.enable()

                transcribe_button = ui.button(
                    "Transcrever e criar legendas", icon="subtitles", on_click=transcribe
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
                            {"name": "start", "label": "Início (s)", "field": "start"},
                            {"name": "end", "label": "Fim (s)", "field": "end"},
                            {"name": "text", "label": "Texto", "field": "text", "align": "left"},
                        ],
                        rows=[{"id": i, **segment} for i, segment in enumerate(record["segments"])],
                        selection="single",
                        on_select=select,
                        pagination=10,
                    ).classes("w-full")
                    correction = ui.textarea("Corrigir o trecho selecionado").props("outlined rows=3")

                    async def save():
                        if state["segment"] is None:
                            ui.notify("Selecione um trecho na tabela.", type="warning")
                            return
                        updated = await action(
                            service.edit_transcript, record["id"], state["segment"], correction.value
                        )
                        if updated:
                            state["record"] = updated
                            result_area.refresh()

                    ui.button("Salvar correção e atualizar legendas", icon="save", on_click=save).props("no-caps")
                    with ui.row():
                        ui.button("Baixar TXT", on_click=lambda: ui.download.file(record["txt"])).props("flat no-caps")
                        ui.button("Baixar SRT", on_click=lambda: ui.download.file(record["srt"])).props("flat no-caps")
                with ui.expansion("Traduzir as legendas no meu PC", icon="translate").classes("w-full"):
                    ui.label(
                        "A tradução usa o modelo local NLLB-200 (download inicial). Revise o resultado. O modelo tem licença CC-BY-NC-4.0."
                    ).classes("studio-muted")
                    source = ui.select(
                        LANGUAGES,
                        value=record["language"] if record["language"] in LANGUAGES else "pt",
                        label="Idioma de origem",
                    ).props("outlined")
                    target = ui.select(
                        LANGUAGES, value="en" if source.value != "en" else "pt", label="Traduzir para"
                    ).props("outlined")

                    async def translate():
                        translated = await action(
                            service.translate_transcript, record["id"], source.value, target.value
                        )
                        if translated:
                            state["record"] = translated
                            previous.set_options({t["id"]: t["name"] for t in service.transcripts()})
                            result_area.refresh()

                    ui.button("Criar versão traduzida", icon="translate", on_click=translate).props("no-caps")

            result_area()

    @ui.page("/revoice")
    def revoice_page():
        state = {"output": None}
        with shell("Regravar com outra voz"):
            ui.label("Outra voz para o seu texto.").classes("studio-title")
            ui.label(
                "Use uma transcrição revisada ou traduzida e gere uma nova narração. Isso é regravação por texto: não preserva exatamente a emoção, o ritmo ou a sincronização do original."
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full"):
                transcript = ui.select(
                    {t["id"]: t["name"] for t in service.transcripts()}, label="Transcrição salva"
                ).props("outlined")
                text = ui.textarea("Texto revisado para regravar").props("outlined rows=8")

                async def load():
                    result = await action(service.transcript, transcript.value)
                    if result:
                        text.set_value(result["text"])

                ui.button("Carregar texto", on_click=load).props("flat no-caps")
                voice = ui.select({v["id"]: v["name"] for v in service.read()["voices"]}, label="Voz de destino").props(
                    "outlined"
                )
                saved_model = ui.select(
                    {model["id"]: model["name"] for model in service.models()},
                    value="original",
                    label="Modelo para regravar",
                ).props("outlined")
                ui.link("Cadastrar outro modelo", "/models").classes("text-sm")
                trained_model = ui.select(
                    service.projects(), label="Usar um projeto treinado (opcional)", clearable=True
                ).props("outlined")
                trained_version = ui.select({}, label="Checkpoint do projeto treinado").props("outlined")

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
                    "A qualidade e o idioma da voz dependem do checkpoint F5-TTS. Os modelos originais são voltados a inglês e chinês."
                ).classes("studio-muted text-sm")

                async def generate():
                    if not voice.value:
                        ui.notify("Escolha uma voz salva.", type="warning")
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

                ui.button("Gerar nova narração", icon="record_voice_over", on_click=generate).props("no-caps")
                player = ui.audio("").classes("w-full")
                download = ui.button(
                    "Baixar WAV", icon="download", on_click=lambda: ui.download.file(state["output"]["path"])
                ).props("flat no-caps")
                download.disable()

    @ui.page("/audio-tools")
    def audio_tools_page():
        paths, state = [], {"output": None}
        with shell("Ferramentas de áudio"):
            ui.label("Prepare e exporte seu áudio.").classes("studio-title")
            ui.label(
                "Ferramentas locais com FFmpeg. A redução de ruído é simples: não equivale a separar voz, música ou falantes com IA."
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full"):
                status = ui.label("Envie o áudio que deseja processar.")
                audio_upload(paths, status)
                effect = ui.select(
                    {
                        "none": "Só converter o formato",
                        "normalize": "Equilibrar volume",
                        "noise": "Reduzir ruído contínuo",
                        "silence": "Cortar silêncios",
                    },
                    value="normalize",
                    label="O que fazer?",
                ).props("outlined")
                fmt = ui.select({"wav": "WAV", "mp3": "MP3"}, value="wav", label="Formato de saída").props("outlined")

                async def process():
                    result = await action(service.process_audio, paths[-1] if paths else "", effect.value, fmt.value)
                    if result:
                        state["output"] = result
                        player.set_source(result)
                        download.enable()
                        status.set_text("Arquivo pronto. Ouça antes de usar.")

                ui.button("Processar no meu PC", icon="tune", on_click=process).props("no-caps")
                player = ui.audio("").classes("w-full")
                download = ui.button(
                    "Baixar arquivo", icon="download", on_click=lambda: ui.download.file(state["output"])
                ).props("flat no-caps")
                download.disable()
